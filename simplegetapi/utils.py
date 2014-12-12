import inspect

if "unicode" not in globals():
    # Python 3.x compatibility
    unicode = str

try:
    from common import enum as enummodule
    has_common_enum = True
except:
    has_common_enum = False

from django.db.models.related import RelatedObject

def is_enum(obj):
    if not has_common_enum:
        return False
    return inspect.isclass(obj) and issubclass(obj, enummodule.Enum)
    
def get_orm_fields(obj):
    for field in obj._meta.fields + \
        obj._meta.many_to_many + \
        obj._meta.get_all_related_objects() + \
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
            field_name = field.name
    
        yield field_name, field

