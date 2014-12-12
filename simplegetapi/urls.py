from django.conf.urls import patterns, include, url

urlpatterns = patterns('',
	url(r'^/(?:([^/]+)(?:/(\d+))?)?$', 'simplegetapi.views.api_request'),
)
