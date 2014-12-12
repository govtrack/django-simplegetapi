import datetime, json, csv, lxml.etree, decimal

try:
    # Python 2.x
    from StringIO import StringIO
except ImportError:
    # Python 3.x
    from io import StringIO
    unicode = str
    long = int

from django.http import HttpResponse
from django.db.models import Model
from django.db.models.fields.related import ForeignKey, ManyToManyField
from django.db.models.related import RelatedObject

from simplegetapi.utils import is_enum, enum_value_to_key_and_label, get_orm_fields

def serialize_object(obj, recurse_on=[], requested_fields=None):
    """Serializes a Python object to JSON-able data types (listed in the 1st if block below)."""
    
    if isinstance(obj, (str, unicode, int, long, float, list, tuple, dict)) or obj is None:
        # basic data type
        return obj
        
    elif isinstance(obj, (datetime.date, datetime.datetime)):
        # dates and datetimes
        return obj.isoformat()

    elif isinstance(obj, decimal.Decimal):
        # Convert Decimals to floats.
        return float(obj)
        
    elif isinstance(obj, Model):
        # ORM instances
        
        ret = { }
        
        # If requested_fields is set, get the list of fields to actually pull data from.
        # requested_fields supports field__field chaining, so just take the first part
        # of each specified field.
        local_fields = [f.split("__", 1)[0] for f in requested_fields] if requested_fields is not None else None
        
        # Loop through the fields on this model. Be sure to process only
        # fields that will not cause additional database queries. ForeignKey,
        # ManyToMany and those sorts of fields should be specified in a
        # recurse_on setting so that they go into the prefetch list.
        for field_name, field in get_orm_fields(obj):
            # Is the user requesting particular fields? If so, check that this is a requested field.
            if local_fields is not None and field_name not in local_fields:
                continue
                
            # Don't recurse on models except where explicitly allowed. And if we aren't going to
            # recurse on this field, stop here so we don't incur a database lookup.
            #
            # For ForeignKeys, instead output the object ID value instead (which the ORM has already
            # cached).
            #
            # RelatedObject fields are reverse-relations, so we don't have the ID. Just skip
            # those.
            #
            # Other relation fields don't do a query when we access the attribute, so it is safe
            # to check those later. Those return RelatedManagers. We check those later.
            if isinstance(field, ForeignKey) and field_name not in recurse_on:
                ret[field_name] = getattr(obj, field_name + "_id")
                continue
            if isinstance(field, RelatedObject) and field_name not in recurse_on:
                continue
                
            # Get the field value.
            if not isinstance(field, (str, unicode)):
                # for standard fields
                try:
                    v = getattr(obj, field_name)
                except:
                    # some fields like OneToOne fields raise a DoesNotExist here
                    # if there is no related object.
                    v = None
            else:
                # for api_additional_fields
                v = obj.api_additional_fields[field] # get the attribute or function
                if not callable(v):
                    # it's an attribute name, so pull the value from the attribute
                    v = getattr(obj, v)
                    if callable(v):
                        # it's a bound method on the object, so call it to get the value
                        v = v()
                else:
                    # the value is a function itself, so call it passing the object instance
                    v = v(obj)
            
            # When serializing inside objects, if we have a field_name__subfield
            # entry in recurse_on, pass subfield to the inside serialization.
            sub_recurse_on = [r[len(field_name)+2:] for r in recurse_on if r.startswith(field_name + "__")]

            # Likewise for user-specified fields in requested_fields.
            sub_fields = [r[len(field_name)+2:] for r in requested_fields if r.startswith(field_name + "__")] if requested_fields is not None else None

            # Get the choices for the field, if there are any.
            choices = getattr(field, "choices", None)

            # For ManyToMany-type fields, serialize the related objects into a list.
            if isinstance(field, ManyToManyField) or str(type(v)) == "<class 'django.db.models.fields.related.RelatedManager'>":
                # Now that we know this is a related field, check that we are allowed to recurse
                # into it. If not, just skip the field entirely. Since we might have an unbounded
                # list of related objects, it is a bad idea to include even the IDs of the objects
                # unless the model author says that is OK.
                if field_name in recurse_on:
                    ret[field_name] = [serialize_object(vv, recurse_on=sub_recurse_on, requested_fields=sub_fields) for vv in v.all()]
                
            # For enumerations, output the key and label and not the raw database value.
            elif v is not None and is_enum(choices):
                key, label = enum_value_to_key_and_label(choices, v)
                ret[field_name] = key
                if label:
                    ret[field_name + "_label"] = label
                
            # For all other values, serialize by recursion.
            else:
                ret[field_name] = serialize_object(v, recurse_on=sub_recurse_on, requested_fields=sub_fields)
                
        return ret
        
    # For all other object types, convert to unicode.
    else:
        return unicode(obj)
            
def serialize_response_json(response):
    """Convert the response dict to JSON."""
    ret = json.dumps(response, sort_keys=True, ensure_ascii=False, indent=True)
    ret = ret.encode("utf8")
    resp = HttpResponse(ret, content_type="application/json; charset=utf-8")
    resp["Content-Length"] = len(ret)
    resp["Generated-At"] = datetime.datetime.now().isoformat()
    return resp

def serialize_response_jsonp(response, callback_name):
    """Convert the response dict to JSON."""
    ret = callback_name + "("
    ret += json.dumps(response, sort_keys=True, ensure_ascii=False, indent=True)
    ret += ");"
    ret = ret.encode("utf8")
    resp = HttpResponse(ret, content_type="application/javascript; charset=utf-8")
    resp["Content-Length"] = len(ret)
    return resp

def serialize_response_xml(response):
    """Convert the response dict to XML."""
    
    def make_node(parent, obj):
        if isinstance(obj, (str, unicode)):
            parent.text = obj
        elif isinstance(obj, (int, long, float)):
            parent.text = unicode(obj)
        elif obj is None:
            parent.text = "null"
        elif isinstance(obj, (list, tuple)):
            for n in obj:
                m = lxml.etree.Element("item")
                parent.append(m)
                make_node(m, n)
        elif isinstance(obj, dict):
            for key, val in sorted(obj.items(), key=lambda kv : kv[0]):
                n = lxml.etree.Element(key)
                parent.append(n)
                make_node(n, val)
        else:
            raise ValueError("Unhandled data type in XML serialization: %s" % unicode(type(obj)))
    
    root = lxml.etree.Element("response")
    make_node(root, response)
    
    ret = lxml.etree.tostring(root, encoding="utf8", pretty_print=True)
    resp = HttpResponse(ret, content_type="text/xml")
    resp["Content-Length"] = len(ret)
    return resp
    
def serialize_response_csv(response, is_list, requested_fields, format):
    if is_list:
        response = response["objects"]
    else:
        response = [response]
    
    if requested_fields is None:
        # Recursively find all keys in the object, making keys like
        # a__b when we dive into dicts within dicts.
        def get_keys(obj, prefix):
            ret = []
            for key in obj.keys():
                if not isinstance(obj[key], dict):
                    ret.append(prefix + key)
                else:
                    for inkey in get_keys(obj[key], prefix + key + "__"):
                        ret.append(inkey)
            return ret
        requested_fields = []
        for item in response:
            for key in get_keys(item, ""):
                if key not in requested_fields:
                    requested_fields.append(key)
        requested_fields.sort()
                
    # write CSV to buffer
    raw_data = StringIO.StringIO()
    writer = csv.writer(raw_data)
    writer.writerow(requested_fields)
    def get_value_recursively(item, key):
        for k in key.split("__"):
            if not isinstance(item, dict): return None
            item = item.get(k, None)
        return item
    def format_value(v):
        if v != None: v = unicode(v).encode("utf8")
        return v
    for item in response:
        writer.writerow([format_value(get_value_recursively(item, c)) for c in requested_fields])
        
    raw_data = raw_data.getvalue()
    if (len(raw_data) > 500000 and format == "csv") or format == "csv:attachment":
        resp = HttpResponse(raw_data, content_type="text/csv")
        resp['Content-Disposition'] = 'attachment; filename="query.csv"'
    #elif format == "csv:inline":
        #resp = HttpResponse(raw_data, content_type="text/csv")
        #resp['Content-Disposition'] = 'inline; filename="query.csv"'
    else:
        resp = HttpResponse(raw_data, content_type="text/plain")
        resp['Content-Disposition'] = 'inline'
    resp["Content-Length"] = len(raw_data)
    return resp