django-simplegetapi
===================

Because the world needs an even simpler Django app for making a read-only API.

Advantages:
-----------

* Works over existing ORM models with little extra configuration needed.
* Can query the database directly via the ORM or via a Django Haystack SearchQuerySet to make full-text search queries via Solr or any Haystack backend.
* JSON, JSONP, CSV, and XML output formats.
* Automatic documentation generation.
* Nice handling of model fields with choices that use `common.enum`.
* Filtering is only allowed on indexed fields to prevent runaway queries.

Dependencies:
-------------

* Python 2.x.
* lxml.etree for the XML output format.

Usage:
------

1) Put `simplegetapi` in your Python path and add `simplegetapi` to your INSTALED_APPS.

2) Create a URLconf entry for API GET requests.

    url(r'^api/v1/([^/]+)(?:/(\d+))?', 'api_request'),

3) Create a GET view.

For ORM-backed models use:

	from simplegetapi.views import do_api_call

	api_model_names = {
		"person": MyPersonModel,
	}

	def api_request(request, model_name, obj_id):
		model = api_model_names[model_name]
		qs = model.objects.all()
		return do_api_call(request, model, qs, obj_id)

For Haystack-backed models, replace `qs` with:

	from haystack.query import SearchQuerySet
	qs = SearchQuerySet().models(model)

4) Make a documentation page.

Add a URLconf entry:

    url(r'^developers/api$', 'api_documentation'),

And make a view:

	from simplegetapi.views import build_api_documentation

	def api_documentation(request):
		baseurl = "http://%s/api/v1/" % request.META["HTTP_HOST"]

		apis = []
		for model_name, model in api_model_names.items():
			apis.append(
				(model, build_api_documentation(model, model.objects.all()) )
			)

		return render_to_response('simplegetapi/documentation.html', {
				"baseurl": baseurl,
				"apis": apis,
			},
			RequestContext(request))

5) Customize your models.

Optionally add any of the following to your models.

`api_recurse_on`: A list of model ForeignKey, OneToOneField, or ManyToManyField fields that should be walked recursively in API outputs. If a field of one of these types is not mentioned in this list, it will be included in API responses as just the primary key (ForienKey, OneToOneField) or list of primary keys (ManyToManyField) and not as an embedded object.

`api_recurse_on_single`: A list of fields as in `api_recurse_on` that additionally get recursively embedded in response outputs but only in API requests to a single object instance (not a query with filters).

`api_additional_fields`: A mapping from new fields to add to API responses to functions that generate those values.

`api_filter_if`: A mapping from field names to a tuple of what other fields must be specified in the query for filtering on the field to be allowed. Besides this, `db_index=True` fields and any prefix of a `unique_together` allow filtering.

`api_example_id`: The primary key of an example object to use in the automatic API documentation.

`api_example_parameters`: A dict giving some sample parameters to an API request to use as an example in the automatic API documentation.

Notes
-----

TODO: Document extended configuration options for Haystack.

TODO: Document how the enum class works. And its doc help text.

