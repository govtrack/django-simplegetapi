from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed, Http404, QueryDict
from django.db.models import DateField, DateTimeField, BooleanField
from django.db.models.fields.related import ForeignKey, ManyToManyField
from django.db.models.related import RelatedObject
from django.shortcuts import get_object_or_404, render
from django.core.urlresolvers import reverse
from django.conf import settings
import csv, json, datetime, lxml, urllib
import dateutil.parser

from simplegetapi.utils import is_enum, enum_key_to_value, enum_get_values, get_orm_fields
from simplegetapi.serializers import serialize_object, serialize_response_json, serialize_response_jsonp, serialize_response_xml, serialize_response_csv

def get_api_models():
    if not hasattr(settings, 'API_MODELS') or not isinstance(settings.API_MODELS, dict):
        raise Exception("The API_MODELS setting is not configured.")

    def resolve_model_name(model_name):
        from django.apps import apps # Django 1.7+
        try:
            app_label, model_name = model_name.split('.', 1)
            return apps.get_model(app_label=app_label, model_name=model_name)
        except:
            raise Exception("The API_MODELS setting is not configured properly. Invalid model: %s" % model_name)

    return { api_name: resolve_model_name(model_name) for api_name, model_name in settings.API_MODELS.items() }

def api_request(request, model_name, obj_id):
    # Get the ORM model.
    models = get_api_models()
    if not model_name in models:
        raise Http404(model_name)
    model = models[model_name]

    # Pass off to main function.
    return do_api_call(request, model, model.objects.all(), obj_id)

def api_documentation(request):
    baseurl = request.build_absolute_uri(reverse(api_request))
    apis = []
    for api_name, model in get_api_models().items():
        apis.append(
            (api_name, build_api_documentation(model, model.objects.all()) )
        )
    return render(request, 'simplegetapi/documentation.html', {
            "baseurl": baseurl,
            "apis": apis,
        })

def do_api_call(request, model, qs, id):
    """Processes an API request for a given ORM model, queryset, and optional ORM instance ID."""

    # Sanity checks.

    if type(qs).__name__ not in ("QuerySet", "SearchQuerySet"):
        raise Exception("Invalid use. Pass a QuerySet or Haystack SearchQuerySet.")

    # Handle a CORS preflight request by allowing cross-domain access to any information
    # provided by the API.
    if request.method == "OPTIONS":
        resp = HttpResponse("", content_type="text/plain; charset=UTF-8")
        resp["Access-Control-Allow-Origin"] = "*"
        resp["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp["Access-Control-Allow-Headers"] = "Authorization,Content-Type,Accept,Origin,User-Agent,DNT,Cache-Control,X-Mx-ReqToken,Keep-Alive,X-Requested-With,If-Modified-Since"
        resp["Access-Control-Max-Age"] = "1728000"
        return resp

    if request.method != "GET":
        # This is a GET-only API.
        return HttpResponseNotAllowed(["GET"])
    
    # The user can specify which fields he wants as a comma-separated list. Also supports
    # field__field chaining for related objects.
    requested_fields = [f.strip() for f in request.GET.get("fields", "").split(',') if f.strip() != ""]
    if len(requested_fields) == 0: requested_fields = None
    
    # Process the call.
    if id == None:
        response = do_api_search(model, qs, request.GET, requested_fields)
    else:
        response = do_api_get_object(model, id, requested_fields)
        
    # Return the result immediately if it is an error condition.
    if isinstance(response, HttpResponse):
        return response
        
    # Add some debugging info to output.
    if settings.DEBUG:
        from django.db import connections
        sqls = { }
        if "meta" in response:
            response["meta"]["sql_debug"] = sqls
        else:
            response["_sql_debug"] = sqls
        for con in connections:
            sqls[con] = connections[con].queries

    # Return results.
    format = request.GET.get('format', 'json')
    if format == "json":
        resp = serialize_response_json(response)
        
    elif format == "jsonp":
        resp = serialize_response_jsonp(response, request.GET.get("callback", "callback"))
        
    elif format == "xml":
        resp = serialize_response_xml(response)
        
    elif format in ("csv", "csv:attachment", "csv:inline"):
        resp = serialize_response_csv(response, id == None, requested_fields, format)
        
    else:
        return HttpResponseBadRequest("Invalid response format: %s." % format)

    # Enable CORS. Allow cross-domain access to anything provided by the API.
    resp["Access-Control-Allow-Origin"] = "*"

    return resp
        
def do_api_search(model, qs, request_options, requested_fields):
    """Processes an API call search request, i.e. /api/modelname?..."""
    
    qs_type = type(qs).__name__
    
    # Get model information specifying how to format API results for calls rooted on this model.
    recurse_on = getattr(model, "api_recurse_on", [])

    # Apply filters specified in the query string.

    qs_sort = None
    qs_filters = { }

    try:
        # Python 2.x
        querystringargs = request_options.iterlists()
    except:
        # Python 3.x
        querystringargs = request_options.lists()

    for arg, vals in querystringargs:
        if arg in ("offset", "limit", "format", "fields", "callback"):
            # These aren't filters.
            pass
        
        elif arg in ("sort", "order_by"):
            # ?sort=fieldname or ?sort=-fieldname
            
            if len(vals) != 1:
                return HttpResponseBadRequest("Invalid query: Multiple sort parameters.")
                
            try:
                qs = qs.order_by(vals[0]) # fieldname or -fieldname
            except Exception as e:
                return HttpResponseBadRequest("Invalid sort: %s" % repr(e))

            qs_sort = (vals[0], "+")
            if vals[0].startswith("-"): qs_sort = (vals[0][1:], "-")

        elif arg == "q" and qs_type == "SearchQuerySet":
            # For Haystack searches, 'q' is a shortcut for the content= filter which
            # does Haystack's full text search.
            
            if len(vals) != 1:
                return HttpResponseBadRequest("Invalid query: Multiple %s parameters." % arg)
            qs = qs.filter(content=vals[0])

        else:
            # This is a regular field filter.
            
            # split fieldname__operator into parts
            arg_parts = arg.rsplit("__", 1) # (field name, ) or (field name, operator)
            
            if len(vals) > 1:
                # If the filter argument is specified more than once, Django gives us the values
                # as an array in vals. When used this way, force the __in operator and don't let
                # the user specify it explicitly.
                arg_parts = [arg, "in"]
                
            elif len(arg_parts) == 2 and arg_parts[1] not in ("contains", "exact", "gt", "gte", "lt", "lte", "in", "startswith", "range"):
                # If the operator isn't actually an operator, it's a sub-field name and the user
                # wants the implicit __exact operator.
                # e.g. field1__field2 means ('field1__field12', 'exact')
                arg_parts[0] += "__" + arg_parts[1]
                arg_parts.pop()
                
            # If there's no __ in the field name (or we adjusted it above), add the implicit __exact operator.
            if len(arg_parts) == 1: arg_parts.append("exact") # default operator
            fieldname, matchoperator = arg_parts
            
            # Get the model field. For Haystack queries, this filter may not correspond to a model field.
            try:
                modelfield = model._meta.get_field(fieldname)
            except:
                modelfield = None

            if matchoperator in ("in", "range"):
                # Allow the | as a separator to accept multiple values (unless the field was specified
                # multiple times as query parameters).
                if len(vals) == 1:
                    vals = vals[0].split("|")
                    
            try:
                vals = [normalize_field_value(v, model, modelfield) for v in vals]
            except ValueError as e:
                return HttpResponseBadRequest("Invalid value for %s filter: %s" % (fieldname, str(e)))
                
            try:
                if matchoperator not in ("in", "range"):
                    # Single-value operators.
                    qs = qs.filter(**{ fieldname + "__" + matchoperator: vals[0] })
                else:
                    # Multi-value operators.
                    if matchoperator == "range" and len(vals) != 2:
                        return HttpResponseBadRequest("The range operator requires the range to be specified as two values separated by a pipe character (e.g. 100|200).")
                    if matchoperator == "in" and len(vals) == 0:
                        return HttpResponseBadRequest("The in operator requires an argument.")
                    
                    qs = qs.filter(**{ fieldname + "__" + matchoperator: vals })
            except Exception as e:
                return HttpResponseBadRequest("Invalid value for %s filter: %s" % (fieldname, repr(e)))
                
            qs_filters[fieldname] = (matchoperator, modelfield)
    
    
    # Is this a valid set of filters and sort option?
    
    indexed_fields, indexed_if = get_model_filterable_fields(model, qs_type)

    # Check the sort field is OK.
    if qs_sort and qs_sort[0] not in indexed_fields:
        return HttpResponseBadRequest("Cannot sort on field: %s" % qs_sort[0])
        
    # Check the filters are OK.
    for fieldname, (modelfield, operator) in qs_filters.items():
        if fieldname not in indexed_fields and fieldname not in indexed_if:
            return HttpResponseBadRequest("Cannot filter on field: %s" % fieldname)                
        
        for f2 in indexed_if.get(fieldname, []):
            if f2 not in qs_filters:
                return HttpResponseBadRequest("Cannot filter on field %s without also filtering on %s" %
                    (fieldname, ", ".join(indexed_if[fieldname])))
        
    # Form the response.

    # Get total count before applying offset/limit.
    try:
        count = qs.count()
    except ValueError as e:
        return HttpResponseBadRequest("A parameter is invalid: %s" % str(e))
    except Exception as e:
        return HttpResponseBadRequest("Something is wrong with the query: %s" % repr(e))

    # Apply offset/limit.
    try:
        offset = int(request_options.get("offset", "0"))
        limit = int(request_options.get("limit", "100"))
    except ValueError:
        return HttpResponseBadRequest("Invalid offset or limit.")
        
    if limit > 6000:
        return HttpResponseBadRequest("Limit > 6000 is not supported. Consider using our bulk data instead.")

    if qs_type == "QuerySet":
        # Don't allow very high offset values because MySQL fumbles the query optimization.
        if offset > 10000:
            return HttpResponseBadRequest("Offset > 10000 is not supported for this data type. Try a __gt filter instead.")

    qs = qs[offset:offset + limit]

    # Bulk-load w/ prefetch_related, but keep order.
    
    if qs_type == "QuerySet":
        # For Django ORM QuerySets, just add prefetch_related based on the fields
        # we're allowed to recurse inside of.
        objs = qs.prefetch_related(*recurse_on) 
    elif qs_type == "SearchQuerySet":
        # For Haystack SearchQuerySets, we need to get the ORM instance IDs,
        # pull the objects in bulk, and then sort by the original return order.
        ids = [entry.pk for entry in qs]
        id_index = { int(id): i for i, id in enumerate(ids) }
        objs = list(model.objects.filter(id__in=ids).prefetch_related(*recurse_on))
        objs.sort(key = lambda ob : id_index[int(ob.id)])
    else:
        raise Exception(qs_type)

    # Serialize.
    return {
        "meta": {
            "offset": offset,
            "limit": limit,
            "total_count": count,
        },
        "objects": [serialize_object(s, recurse_on=recurse_on, requested_fields=requested_fields) for s in objs],
    }
 
def normalize_field_value(v, model, modelfield):
    # Convert "null" to None.
    if v.lower() == "null":
        if modelfield and not modelfield.null:
            raise ValueError("Field cannot be null.")
        return None
        
    is_bool = False
    if modelfield and isinstance(modelfield, (BooleanField)):
        is_bool = True
    # and for our way of specifying additional Haystack fields...
    for fieldname, fieldtype in getattr(model, "haystack_index_extra", []):
        if modelfield and fieldname == modelfield.name and fieldtype in ("Boolean"):
            is_bool = True
    if is_bool:
        if v == "true":
            return True
        if v == "false":
            return False
        raise ValueError("Invalid boolean (must be 'true' or 'false').")

    # If the model field's choices is a common.enum.Enum instance,
    # then the filter specifies the enum key, which has to be
    # converted to an integer.
    choices = modelfield.choices if modelfield else None
    if choices and is_enum(choices):
        try:
            # Convert the string value to the raw database integer value.
            return enum_key_to_value(choices, v)
        except: # field is not a model field, or enum value is invalid (leave as original)
            raise ValueError("%s is not a valid value; possibly values are %s" % (v, ", ".join(c.key for c in choices.values())))

    # If this is a filter on a datetime field, parse the date in ISO format
    # because that's how we serialize it. Normally you can just pass a string
    # value to .filter(). The conversion takes place in the backend. MySQL
    # will recognize ISO-like formats. But Haystack with Solr will only
    # recognize the Solr datetime format. So it's better to parse now and
    # pass a datetime instance.
    
    is_dt = False
    if modelfield and isinstance(modelfield, (DateField, DateTimeField)):
        is_dt = True
    # and for our way of specifying additional Haystack fields...
    for fieldname, fieldtype in getattr(model, "haystack_index_extra", []):
        if modelfield and fieldname == modelfield.name and fieldtype in ("Date", "DateTime"):
            is_dt = True
    if is_dt:
        # Let any ValueErrors percolate up. Seems like TypeError also can occur ('2014-xx-xx').
        try:
            return dateutil.parser.parse(str(v), default=datetime.datetime.min, ignoretz=not settings.USE_TZ)
        except TypeError:
            raise ValueError("Invalid date.")
        
    return v

def get_model_filterable_fields(model, qs_type):
    if qs_type == "QuerySet":
        # The queryset is a Django ORM QuerySet. Allow filtering/sorting on all Django ORM fields
        # with db_index=True. Additionally allow filtering on a prefix of any Meta.unqiue.
        
        # Get the fields with db_index=True. The id field is implicitly indexed.
        indexed_fields = set(f.name for f in model._meta.fields if f.name == 'id' or f.db_index)

        # For every (a,b,c) in unique_together, make a mapping like:
        #  a: [] # no dependencies, but indexed
        #  b: a
        #  c: (a,b)
        # indicating which other fields must be filtered on to filter one of these fields.
        indexed_if = { }
        for unique_together in model._meta.unique_together:
            for i in xrange(len(unique_together)):
                indexed_if[unique_together[i]] = unique_together[:i]

        # Also allow the model to specify other conditions.
        indexed_if.update( getattr(model, "api_filter_if", {}) )
        
    elif qs_type == "SearchQuerySet":
        # The queryset is a Haystack SearchQuerySet. Allow filtering/sorting on fields indexed
        # in Haystack, as specified in the haystack_index attribute on the model (a tuple/list)
        # and the haystack_index_extra attribute which is a tuple/list of tuples, the first
        # element of which is the Haystack field name.
        indexed_fields = set(getattr(model, "haystack_index", [])) | set(f[0] for f in getattr(model, "haystack_index_extra", []))
        
        indexed_if = { }
        
    else:
        raise Exception(qs_type)

    return indexed_fields, indexed_if

def do_api_get_object(model, id, requested_fields):
    """Gets a single object by primary key."""
    
    # Object ID is known.
    obj = get_object_or_404(model, id=id)

    # Get model information specifying how to format API results for calls rooted on this model.
    recurse_on = list(getattr(model, "api_recurse_on", []))
    recurse_on += list(getattr(model, "api_recurse_on_single", []))

    # Serialize.
    return serialize_object(obj, recurse_on=recurse_on, requested_fields=requested_fields)

def build_api_documentation(model, qs):
    indexed_fields, indexed_if = get_model_filterable_fields(model, type(qs).__name__)
    
    ex_id = getattr(model, "api_example_id", None)

    if ex_id:
        example_data = do_api_get_object(model, ex_id, None)
    else:
        qd = QueryDict("limit=5").copy()
        for k, v in getattr(model, "api_example_parameters", {}).items():
            qd[k] = v
        example_data = do_api_search(model, qs, qd, None)
    example_data = json.dumps(example_data, sort_keys=True, ensure_ascii=False, indent=4)

    recurse_on = set(getattr(model, "api_recurse_on", []))
    recurse_on_single = set(getattr(model, "api_recurse_on_single", []))
    
    fields_list = []
    for field_name, field in get_orm_fields(model):
        field_info = { }

        # Indexed?
        if field_name in indexed_fields:
            field_info["filterable"] = "Filterable with operators. Sortable."
        if field_name in indexed_if:
            if len(indexed_if[field_name]) == 0:
                field_info["filterable"] = "Filterable."
            else:
                field_info["filterable"] = "Filterable when also filtering on " + " and ".join(indexed_if[field_name]) + "."
                
        if "unicode" not in globals():
            # Python 3.x compatibility
            unicode = str
        if isinstance(field, (str, unicode)):
            # for api_additional_fields
            v = model.api_additional_fields[field] # get the attribute or function
            if not callable(v):
                # it's an attribute name, so pull the value from the attribute,
                # which hopefully gives something with a docstring
                v = getattr(model, v)
                field_info["help_text"] = v.__doc__
            
        elif isinstance(field, RelatedObject):
            # for get_all_related_objects()
            if field_name not in (recurse_on|recurse_on_single): continue
            field_info["help_text"] = "A list of %s instances whose %s field is this object. Each instance is returned as a JSON dict (or equivalent in other output formats)." % (field.model.__name__, field.field.name)
            if field_name not in recurse_on:
                field_info["help_text"] += " Only returned in a single-object query."
            
        else:
            # for regular fields
            field_info["help_text"] = field.help_text

            if isinstance(field, ForeignKey):
                if field_name not in (recurse_on|recurse_on_single):
                    field_info["help_text"] += " Returned as an integer ID."
                else:
                    if field_name in recurse_on:
                        field_info["help_text"] += " The full object is included in the response as a JSON dict (or equivalent in other output formats)."
                    else:
                        field_info["help_text"] += " In a list/search query, only the id is returned. In a single-object query, the full object is included in the response as a JSON dict (or equivalent in other output formats)."
                    if "filterable" in field_info:
                        field_info["filterable"] += " When filtering, specify the integer ID of the target object."

            if isinstance(field, ManyToManyField):
                if field_name not in (recurse_on|recurse_on_single): continue
                field_info["help_text"] += " Returned as a list of JSON dicts (or equivalent in other output formats)."
                if field_name not in recurse_on:
                    field_info["help_text"] += " Only returned in a query for a single object."
                if "filterable" in field_info:
                    field_info["filterable"] += " When filtering, specify the ID of one target object to test if the target is among the values of this field."
                    
            # Except ManyToMany
            elif "filterable" in field_info and field.null:
                field_info["filterable"] += " To search for a null value, filter on the special string 'null'."

            # Choices?
            enum = field.choices
            if is_enum(enum):
                field_info["enum_values"] = enum_get_values(enum)

        # Stupid Django hard-coded text.
        field_info["help_text"] = field_info.get("help_text", "").replace('Hold down "Control", or "Command" on a Mac, to select more than one.', '')

        fields_list.append((field_name, field_info))
    
    fields_list.sort()
    
    if type(qs).__name__ == "SearchQuerySet":
        fields_list.insert(0, ("q", { "help_text": "Filters according to a full-text search on the object.", "filterable": "Filterable (without operators)." }))
    
    return {
        "docstring": model.__doc__,
        "canonical_example": ex_id,
        "example_content": example_data,
        "fields_list": fields_list,
    }
    
