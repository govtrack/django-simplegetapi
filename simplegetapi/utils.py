import inspect, sys

if "unicode" not in globals():
    # Python 3.x compatibility
    unicode = str

try:
    from common import enum as enummodule
    has_common_enum = True
except ImportError:
    has_common_enum = False

if sys.version_info < (3,):
    has_enum = False
else:
    import enum
    has_enum = True

from django.db.models.related import RelatedObject

def is_enum(choices):
    return is_enum_pyenum(choices) or is_enum_commonenum(choices)

def is_enum_pyenum(choices):
    return has_enum and len(choices) > 0 and isinstance(choices[0][0], enum.Enum)

def is_enum_commonenum(choices):
    return has_common_enum and inspect.isclass(choices) and issubclass(choices, enummodule.Enum)

def enum_key_to_value(enumclass, key):
    if is_enum_commonenum(enumclass):
        return int(enumclass.by_key(key))

def enum_get_values(choices):
    if is_enum_commonenum(choices):
        return dict((v.key, { "label": v.label, "description": getattr(v, "search_help_text", None) } ) for v in choices.values())
    if is_enum_pyenum(choices):
        return { k.name: { "label": k.name, "description": "" } for k, v in choices }

def enum_value_to_key_and_label(choices, value):
    if is_enum_commonenum(choices):
        return choices.by_value(value).key, choices.by_value(value).label
    if is_enum_pyenum(choices):
        return (value.name, None)

def get_orm_fields(obj):
    for field in list(obj._meta.get_fields()) + \
        list(getattr(obj, "api_additional_fields", {})):
            
        # Get the field name.
        if isinstance(field, (str, unicode)):
            # for api_additional_fields
            field_name = field
        elif isinstance(field, RelatedObject):
            # for get_all_related_objects()
            field_name = field.get_accessor_name()
        else:
            # for other fields
            if field.auto_created: continue
            field_name = field.name
    
        yield field_name, field

