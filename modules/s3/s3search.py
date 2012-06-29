# -*- coding: utf-8 -*-

""" RESTful Search Methods

    @requires: U{B{I{gluon}} <http://web2py.com>}

    @copyright: 2009-2012 (c) Sahana Software Foundation
    @license: MIT

    Permission is hereby granted, free of charge, to any person
    obtaining a copy of this software and associated documentation
    files (the "Software"), to deal in the Software without
    restriction, including without limitation the rights to use,
    copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the
    Software is furnished to do so, subject to the following
    conditions:

    The above copyright notice and this permission notice shall be
    included in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
    EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
    OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
    NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
    HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
    WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
    FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
    OTHER DEALINGS IN THE SOFTWARE.
"""

import re

try:
    import json # try stdlib (Python 2.6)
except ImportError:
    try:
        import simplejson as json # try external module
    except:
        import gluon.contrib.simplejson as json # fallback to pure-Python module

from gluon import *
from gluon.serializers import json as jsons
from gluon.storage import Storage

from s3crud import S3CRUD
from s3navigation import s3_search_tabs
from s3utils import s3_debug, S3DateTime, s3_get_reference
from s3validators import *
from s3widgets import CheckboxesWidgetS3, S3OrganisationHierarchyWidget

from s3rest import S3FieldSelector

__all__ = ["S3SearchWidget",
           "S3SearchSimpleWidget",
           "S3SearchMinMaxWidget",
           "S3SearchOptionsWidget",
           "S3SearchLocationHierarchyWidget",
           "S3SearchLocationWidget",
           "S3SearchSkillsWidget",
           "S3SearchOrgHierarchyWidget",
           "S3Search",
           "S3LocationSearch",
           "S3OrganisationSearch",
           "S3PersonSearch",
           "S3HRSearch",
           "S3PentitySearch",
           "S3TrainingSearch",
           ]

MAX_RESULTS = 1000
MAX_SEARCH_RESULTS = 200

# =============================================================================
class S3SearchWidget(object):
    """
        Search Widget for interactive search (base class)
    """

    def __init__(self, field=None, name=None, **attr):
        """
            Configures the search option

            @param field: name(s) of the fields to search in
            @param name: ?

            @keyword label: a label for the search widget
            @keyword comment: a comment for the search widget
        """
        self.other = None
        self.field = field

        if not self.field:
            raise SyntaxError("No search field specified.")

        self.attr = Storage(attr)
        if name is not None:
            self.attr["_name"] = name

        self.master_query = None
        self.search_field = None

    # -------------------------------------------------------------------------
    def widget(self, resource, vars):
        """
            Returns the widget

            @param resource: the resource to search in
            @param vars: the URL GET variables as dict
        """

        self.attr = Storage(attr)

        raise NotImplementedError

    # -------------------------------------------------------------------------
    @staticmethod
    def query(resource, value):
        """
            Returns a sub-query for this search option

            @param resource: the resource to search in
            @param value: the value returned from the widget
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    def build_master_query(self, resource):
        """
            Get the master query for the specified field(s)
        """

        db = current.db
        table = resource.table

        master_query = Storage()
        search_field = Storage()

        fields = self.field
        if fields and not isinstance(fields, (list, tuple)):
            fields = [fields]

        # Find the tables, joins and fields to search in
        for f in fields:
            ktable = None
            rtable = None
            component = None
            reference = None
            multiple = False

            if f.find(".") != -1: # Component
                cname, f = f.split(".", 1)
                if cname not in resource.components:
                    continue
                else:
                    component = resource.components[cname]
                ktable = component.table
                ktablename = component.tablename
                pkey = component.pkey
                fkey = component.fkey
                # Do not add queries for empty tables
                if not db(ktable.id > 0).select(ktable.id,
                                                limitby=(0, 1)).first():
                    continue
            else: # this resource
                ktable = table
                ktablename = table._tablename

            if f.find("$") != -1: # Referenced object
                rkey, f = f.split("$", 1)
                if not rkey in ktable.fields:
                    continue
                rtable = ktable
                rtablename = ktablename
                ktablename, key, multiple = s3_get_reference(ktable[rkey])
                if not ktablename:
                    continue
                else:
                    ktable = db[ktablename]
                # Do not add queries for empty tables
                if not db(ktable.id > 0).select(ktable.id,
                                                limitby=(0, 1)).first():
                    continue

            # Master queries
            # @todo: update this for new QueryBuilder (S3ResourceFilter)
            if ktable and ktablename not in master_query:
                query = (resource.accessible_query("read", ktable))
                if "deleted" in ktable.fields:
                    query = (query & (ktable.deleted == "False"))
                join = None
                if reference:
                    if ktablename != rtablename:
                        q = (resource.accessible_query("read", rtable))
                        if "deleted" in rtable.fields:
                            q = (q & (rtable.deleted == "False"))
                    else:
                        q = None
                    if multiple:
                        j = (rtable[rkey].contains(ktable.id))
                    else:
                        j = (rtable[rkey] == ktable.id)
                    if q is not None:
                        join = q & j
                    else:
                        join = j
                j = None
                if component:
                    if reference:
                        q = (resource.accessible_query("read", table))
                        if "deleted" in table.fields:
                            q = (q & (table.deleted == "False"))
                        j = (q & (table[pkey] == rtable[fkey]))
                    else:
                        j = (table[pkey] == ktable[fkey])
                if j is not None and join is not None:
                    join = (join & j)
                elif j:
                    join = j
                if join is not None:
                    query = (query & join)
                master_query[ktable._tablename] = query

            # Search fields
            if ktable and f in ktable.fields:
                if ktable._tablename not in search_field:
                    search_field[ktablename] = [ktable[f]]
                else:
                    search_field[ktablename].append(ktable[f])

        self.master_query = master_query
        self.search_field = search_field

# =============================================================================
class S3SearchSimpleWidget(S3SearchWidget):
    """
        Simple full-text search widget
    """

    def widget(self,
               resource,
               vars=None,
               name=None,
               value=None,
               autocomplete=None):
        """
            Returns the widget

            @param resource: the resource to search in
            @param vars: the URL GET variables as dict
        """

        # SearchAutocomplete must set name depending on the field
        if name:
            self.attr.update(_name=name)

        if "_size" not in self.attr:
            self.attr.update(_size="40")
        if "_name" not in self.attr:
            self.attr.update(_name="%s_search_simple" % resource.name)
        if "_id" not in self.attr:
            self.attr.update(_id="%s_search_simple" % resource.name)
        if autocomplete:
            self.attr.update(_autocomplete=autocomplete)
        self.attr.update(_type="text")

        self.name = self.attr._name


        # Search Autocomplete - Display current value
        self.attr["_value"] = value

        return INPUT(**self.attr)

    # -------------------------------------------------------------------------
    def query(self, resource, value):
        """
            Returns a sub-query for this search option

            @param resource: the resource to search in
            @param value: the value returned from the widget
        """

        # Build the query
        if value and isinstance(value, str):
            values = value.split()

            final_query = None

            # Create a set of queries for each value
            for value in values:
                field_queries = None

                # Create a set of queries that test the current
                # value against each field
                for field in self.field:
                    s = S3FieldSelector(field).lower()
                    field_query = s.like("%%%s%%" % value.lower())

                    # We want a match against any field
                    if field_queries:
                        field_queries = field_query | field_queries
                    else:
                        field_queries = field_query

                # We want all values to be matched
                if final_query:
                    final_query = field_queries & final_query
                else:
                    final_query = field_queries

            return final_query
        else:
            return None


# =============================================================================
class S3SearchMinMaxWidget(S3SearchWidget):
    """
        Min/Max search widget for numeric fields
    """

    def widget(self, resource, vars):
        """
            Returns the widget

            @param resource: the resource to search in
            @param vars: the URL GET variables as dict
        """
        T = current.T
        settings = current.deployment_settings

        self.names = []
        self.method = self.attr.get("method", "range")
        select_min = self.method in ("min", "range")
        select_max = self.method in ("max", "range")

        if not self.search_field:
            self.build_master_query(resource)

        search_field = self.search_field.values()
        if not search_field:
            return SPAN(T("no options available"),
                        _class="no-options-available")

        search_field = search_field[0][0]

        ftype = str(search_field.type)
        input_min = input_max = None
        if ftype == "integer":
            requires = IS_EMPTY_OR(IS_INT_IN_RANGE())
        elif ftype == "date":
            self.attr.update(_class="date")
            requires = IS_EMPTY_OR(IS_DATE(format=settings.get_L10n_date_format()))
        elif ftype == "time":
            self.attr.update(_class="time")
            requires = IS_EMPTY_OR(IS_TIME())
        elif ftype == "datetime":
            self.attr.update(_class="datetime")
            requires = IS_EMPTY_OR(IS_DATETIME(format=settings.get_L10n_datetime_format()))
        else:
            raise SyntaxError("Unsupported search field type")

        self.attr.update(_type="text")
        trl = TR(_class="sublabels")
        tri = TR()

        # dictionaries for storing details of the input elements
        name = self.attr["_name"]
        self.widmin = dict(name="%s_min" % name,
                           label=T("min"),
                           requires=requires,
                           attributes=self.attr)
        self.widmax = dict(name="%s_max" % name,
                           label=T("max"),
                           requires=requires,
                           attributes=self.attr)

        if select_min:
            min_label = self.widget_label(self.widmin)
            min_input = self.widget_input(self.widmin)

            self.names.append(self.widmin["name"])
            trl.append(min_label)
            tri.append(min_input)

        if select_max:
            max_label = self.widget_label(self.widmax)
            max_input = self.widget_input(self.widmax)

            self.names.append(self.widmax["name"])
            trl.append(max_label)
            tri.append(max_input)

        w = TABLE(trl, tri, _class="s3searchminmaxwidget")

        return w

    # -------------------------------------------------------------------------
    @staticmethod
    def widget_label(widget):
        """
            @param widget: dict with the name, label, requires and
                            attributes for the input element
            @return: LABEL
        """
        return LABEL(widget["label"], _for="id-%s" % widget["name"])

    # -------------------------------------------------------------------------
    @staticmethod
    def widget_input(widget):
        """
            @param widget: dict with the name, label, requires and
                            attributes for the input element
            @return: INPUT
        """
        attr = widget["attributes"].copy()
        attr.update(_name=widget["name"],
                    _id="id-%s" % widget["name"])
        return INPUT(requires=widget["requires"], **attr)

    # -------------------------------------------------------------------------
    def validate(self, resource, value):
        """
            Validate the input values of the widget
        """
        T = current.T
        errors = dict()

        select_min = self.method in ("min", "range")
        select_max = self.method in ("max", "range")

        if select_min and select_max:
            vmin = value.get(self.widmin["name"], None)
            vmax = value.get(self.widmax["name"], None)

            if vmax is not None and vmin is not None and vmin > vmax:
                errors[self.widmax["name"]] = \
                     T("Maximum must be greater than minimum")

        return errors or None

    # -------------------------------------------------------------------------
    def query(self, resource, value):
        """
            Returns a sub-query for this search option

            @param resource: the resource to search in
            @param value: the value returned from the widget
        """
        select_min = self.method in ("min", "range")
        select_max = self.method in ("max", "range")

        min_query = max_query = query = None

        if select_min:
            v = value.get(self.widmin["name"], None)
            if v is not None and str(v):
                min_query = S3FieldSelector(self.field) >= v

        if select_max:
            v = value.get(self.widmax["name"], None)
            if v is not None and str(v):
                max_query = S3FieldSelector(self.field) <= v

        if min_query is not None:
            query = min_query

            if max_query is not None:
                query = query & max_query
        else:
            query = max_query

        return query

# =============================================================================
class S3SearchOptionsWidget(S3SearchWidget):
    """
        Option select widget for option or boolean fields

        Displays a search widget which allows the user to search for records
        with fields matching a certain criteria.

        Field must be an integer or reference to work on all versions of
        gluon/sqlhtml.py

        @param represent: If the field is a reference, represent can pass a
                          formatting string with mapping fields to the
                          referenced record.
        @param cols: The number of columns which the options will be
                     displayed in
    """

    def __init__(self, field=None, name=None, options=None, **attr):
        """
            Configures the search option

            @param field: name(s) of the fields to search in
            @param name: used to build the HTML ID of the widget
            @param options: either a value:label dictionary to populate the
                            search widget or a callable to create this

            @keyword label: a label for the search widget
            @keyword comment: a comment for the search widget
        """
        super(S3SearchOptionsWidget, self).__init__(field, name, **attr)
        self.options = options

    # -------------------------------------------------------------------------
    def _get_reference_resource(self, resource):
        """
            If the field is entered as kfield$field, will search field in the
            the referenced resource.
        """
        field = self.field
        kfield = None

        if field.find("$") != -1:
            is_component = True
            kfield, field = field.split("$")
            tablename = resource.table[kfield].type[10:]
            prefix, resource_name = tablename.split("_", 1)
            resource = current.manager.define_resource(prefix,
                                                       resource_name)
        return resource, field, kfield

    # -------------------------------------------------------------------------
    def widget(self, resource, vars):
        """
            Returns the widget

            @param resource: the resource to search in
            @param vars: the URL GET variables as dict
        """
        T = current.T

        resource, field_name, kfield = self._get_reference_resource(resource)

        if "_name" not in self.attr:
            self.attr.update(_name="%s_search_select_%s" % (resource.name,
                                                            field_name))
        self.name = self.attr._name

        # populate the field value from the GET parameter
        if vars and self.name in vars:
            value = vars[self.name]
        else:
            value = None

        # check the field type
        try:
            field = resource.table[field_name]
        except:
            field_type = "virtual"
        else:
            field_type = str(field.type)


        opt_keys = []
        opt_list = []

        if self.options is not None:
            if isinstance(self.options, dict):
                options = self.options
                opt_keys = options.keys()
                opt_list = options.items()
            elif callable(self.options):
                options = self.options()
                opt_keys = options.keys()
                opt_list = options.items()
        else:
            if field_type == "boolean":
                opt_keys = (True, False)
            else:
                # Find unique values of options for that field
                rows = resource.select(field, groupby=field)
                if field_type.startswith("list"):
                    for row in rows:
                        rfield = row[field_name]
                        if rfield != None:
                            try:
                                _opt_keys = rfield.split("|")
                            except:
                                _opt_keys = rfield
                            for opt_key in _opt_keys:
                                opt_keys.append(opt_key)
                else:
                    opt_keys = [row[field_name] for row
                                                in rows
                                                if row[field_name] != None]

        if opt_keys == []:
            msg = self.attr.get("_no_opts", T("No options available"))
            return SPAN(msg, _class="no-options-available")

        if self.options is None:
            # Always use the represent of the widget, if present
            represent = self.attr.represent
            # Fallback to the field's represent
            if not represent or field_type[:9] != "reference":
                represent = field.represent

            # Execute, if callable
            if callable(represent):
                if "show_link" in represent.func_code.co_varnames:
                    opt_list = [(opt_key, represent(opt_key, show_link=False)) for opt_key
                                                                               in opt_keys]
                else:
                    opt_list = [(opt_key, represent(opt_key)) for opt_key
                                                              in opt_keys]
            # Otherwise, feed the format string
            elif isinstance(represent, str) and field_type[:9] == "reference":
                # Use the represent string to reduce db calls
                # Find the fields which are needed to represent:
                db = current.db
                ktable = db[field_type[10:]]
                fieldnames = ["id"]
                fieldnames += re.findall("%\(([a-zA-Z0-9_]*)\)s", represent)
                represent_fields = [ktable[fieldname] for fieldname in fieldnames]
                query = (ktable.id.belongs(opt_keys)) & (ktable.deleted == False)
                represent_rows = db(query).select(*represent_fields).as_dict(key=represent_fields[0].name)
                opt_list = []
                for opt_key in opt_keys:
                    opt_represent = represent % represent_rows[opt_key]
                    if opt_represent:
                        opt_list.append([opt_key, opt_represent])
            else:
                opt_list = [(opt_key, "%s" % opt_key) for opt_key in opt_keys if opt_key]

            # Alphabetise (this will not work as it is converted to a dict),
            # look at IS_IN_SET validator or CheckboxesWidget to ensure
            # that the list opt_list.sort()

            options = dict(opt_list)

        # Dummy field
        opt_field = Storage(name=self.name,
                            requires=IS_IN_SET(options,
                                               multiple=True))
        MAX_OPTIONS = 20
        if len(opt_list) > MAX_OPTIONS:
            # Collapse list Alphabetically into letter headers
            # Could this functionality be implemented in a custom
            # CheckboxesWidget?

            # Split opt_list into a dict with the letter as keys
            # letter_options = { "A" : [ ( n, "A..."),
            #                            ( n, "A..."),
            #                            ...
            #                            ],
            #                    "B" : [ ( n, "B..."),
            #                            ( n, "B..."),
            #                            ...
            #                            ],
            #                     ...
            #                    }
            # and create a list of letters
            letters = []
            letter_options = {}
            for key, label in opt_list:
                letter = label and label[0]
                if letter:
                    letter = letter.upper()
                    if letter not in letter_options:
                        letters.append(letter)
                        letter_options[letter] = [(key, label)]
                    else:
                        letter_options[letter].append((key, label))

            # Ensure that letters contains A & Z
            # (For usability to ensure that the complete range is displayed)
            letters.sort()
            if letters[0] != "A":
                letters.insert(0, "A")
            if letters[-1] != "Z":
                letters.append("Z")

            # Build widget
            widget = DIV()
            count = 0
            options = []
            requires = IS_IN_SET(opt_list, multiple=True)

            from_letter = None
            to_letter = None
            for letter in letters:
                if not from_letter:
                    from_letter = letter
                letter_option = letter_options.get(letter, [])
                count += len(letter_option)
                # Check if the number of options is > MAX_OPTIONS
                if count > MAX_OPTIONS or letter == "Z":
                    if not options:
                        options = letter_option

                    if letter == "Z":
                        to_letter = "Z"
                    # Are these options for a single letter or a range?
                    if to_letter != from_letter:
                        _letter = "%s - %s" % (from_letter, to_letter)
                    else:
                        _letter = from_letter
                    # Letter Label
                    widget.append(DIV(_letter,
                                      _id="%s_search_select_%s_label_%s" %
                                                (resource.name,
                                                 field_name,
                                                 from_letter),
                                      _class="search_select_letter_label"))

                    opt_field = Storage(name=self.name,
                                        requires=IS_IN_SET(options,
                                                           multiple=True))
                    if self.attr.cols:
                        letter_widget = CheckboxesWidgetS3.widget(opt_field,
                                                                  value,
                                                                  cols=self.attr.cols,
                                                                  requires=requires,
                                                                  _class="search_select_letter_widget")
                    else:
                        letter_widget = CheckboxesWidgetS3.widget(opt_field, value,
                                                                  _class="search_select_letter_widget")

                    widget.append(letter_widget)

                    count = 0
                    options = []
                    from_letter = letter
                options += letter_option
                to_letter = letter

        else:
            try:
                if self.attr.cols:
                    widget = CheckboxesWidgetS3.widget(opt_field,
                                                       value,
                                                       cols=self.attr.cols)
                else:
                    widget = CheckboxesWidgetS3.widget(opt_field,
                                                       value)
            except:
                # Some versions of gluon/sqlhtml.py don't support
                # non-integer keys
                return None
        return widget

    # -------------------------------------------------------------------------
    def query(self, resource, value):
        """
            Returns a sub-query for this search option

            @param resource: the resource to search in
            @param value: the value returned from the widget
        """

        if value:
            if not isinstance(value, (list, tuple)):
                value = [value]

            try:
                table_field = resource.table[self.field]
            except:
                table_field = None

            # What do we do if we need to search within a virtual field
            # that is a list:* ?
            if table_field and str(table_field.type).startswith("list"):
                query = S3FieldSelector(self.field).contains(value)
            else:
                query = S3FieldSelector(self.field).belongs(value)

            return query
        else:
            return None

# =============================================================================
class S3SearchLocationHierarchyWidget(S3SearchOptionsWidget):
    """
        Displays a search widget which allows the user to search for records
        by selecting a location from a specified level in the hierarchy.
        - works only for tables with s3.address_fields() in
          i.e. Sites & pr_address
    """

    def __init__(self, name=None, field=None, **attr):
        """
            Configures the search option

            @param name: name of the search widget
            @param field: field containing a hierarchy level to search

            @keyword comment: a comment for the search widget
        """

        gis = current.gis

        self.other = None

        if field:
            if field.find("$") != -1:
                kfield, level = field.split("$")
            else:
                level = field
        else:
            # Default to the currently active gis_config
            config = gis.get_config()
            field = level = config.search_level or "L0"

        hierarchy = gis.get_location_hierarchy()
        if level in hierarchy:
            label = hierarchy[level]
        else:
            label = level

        self.field = field

        super(S3SearchLocationHierarchyWidget, self).__init__(field,
                                                              name,
                                                              **attr)

        self.attr = Storage(attr)
        self.attr["label"] = label
        if name is not None:
            self.attr["_name"] = name

# =============================================================================
class S3SearchLocationWidget(S3SearchWidget):
    """
        Interactive location search widget
        - allows the user to select a BBOX & have only results from within
          that BBOX returned

        @ToDo: Have an option to use a Circular Radius
               http://openlayers.org/dev/examples/regular-polygons.html
        @ToDo: Have an option to use a full Polygon
               Hard to process this as a resource filter
    """

    def __init__(self,
                 field="location_id",
                 name=None, # Needs to be specified by caller
                 **attr):
        """
            Initialise parent class & make any necessary modifications
        """

        S3SearchWidget.__init__(self, field, name, **attr)

    # -------------------------------------------------------------------------
    def widget(self, resource, vars):
        """
            Returns the widget

            @param resource: the resource to search in
            @param vars: the URL GET variables as dict
        """

        format = current.auth.permission.format
        if format == "plain":
            return None

        try:
            from shapely.wkt import loads as wkt_loads
        except ImportError:
            s3_debug("WARNING: %s: Shapely GIS library not installed" % __name__)
            return None

        T = current.T

        # Components

        if "comment" not in self.attr:
            self.attr.update(comment=T("Draw a square to limit the results to just those within the square."))
            #self.attr.update(comment="%s|%s|%s" % (T("Draw a Polygon around the area to which you wish to restrict your search."),
            #                                       T("Click on the map to add the points that make up your polygon. Double-click to finish drawing."),
            #                                       T("To activate Freehand mode, hold down the shift key.")))
        self.comment = self.attr.comment

        # Hidden Field to store the Polygon value in
        polygon_input = INPUT(_id="gis_search_polygon_input",
                              _name=self.attr._name,
                              _class="hide")

        # Map Popup
        # - not added as we reuse the one that comes with dataTables

        # Button to open the Map
        OPEN_MAP = T("Open Map")
        map_button = A(OPEN_MAP,
                       _style="cursor:pointer; cursor:hand",
                       _id="gis_search_map-btn")

        # Settings to be read by static/scripts/S3/s3.gis.js
        js_location_search = """S3.gis.draw_polygon = true;"""

        # The overall layout of the components
        return TAG[""](
                        polygon_input,
                        map_button,
                        #map_popup,
                        SCRIPT(js_location_search)
                      )

    # -------------------------------------------------------------------------
    @staticmethod
    def query(resource, value):
        """
            Returns a sub-query for this search option

            @param resource: the resource to search in
            @param value: the value returned from the widget: WKT format
        """

        if value:
            # @ToDo: Turn this into a Resource filter
            #features = gis.get_features_in_polygon(value,
            #                                       tablename=resource.tablename)

            # @ToDo: A PostGIS routine, where-available
            #        - requires a Spatial DAL?
            from shapely.wkt import loads as wkt_loads
            try:
                shape = wkt_loads(value)
            except:
                s3_debug("WARNING: S3Search: Invalid WKT")
                return None

            bounds = shape.bounds
            lon_min = bounds[0]
            lat_min = bounds[1]
            lon_max = bounds[2]
            lat_max = bounds[3]

            # Return all locations which have a part of themselves inside the BBOX
            # This requires the locations to have their bounds set properly
            # This can be done globally using:
            # gis.update_location_tree()
            query = (S3FieldSelector("location_id$lat_min") <= lat_max) & \
                    (S3FieldSelector("location_id$lat_max") >= lat_min) & \
                    (S3FieldSelector("location_id$lon_min") <= lon_max) & \
                    (S3FieldSelector("location_id$lon_max") >= lon_min)
            return query
        else:
            return None

# =============================================================================
class S3SearchCredentialsWidget(S3SearchOptionsWidget):
    """
        Options Widget to search for HRMs with specified Credentials
    """

    def widget(self, resource, vars):
        manager = current.manager
        c = manager.define_resource("hrm", "credential")
        return S3SearchOptionsWidget.widget(self, c, vars)

    # -------------------------------------------------------------------------
    @staticmethod
    def query(resource, value):
        if value:
            db = current.db
            htable = db.hrm_human_resource
            ptable = db.pr_person
            ctable = db.hrm_credential
            query = (htable.person_id == ptable.id) & \
                    (htable.deleted != True) & \
                    (ctable.person_id == ptable.id) & \
                    (ctable.deleted != True) & \
                    (ctable.job_role_id.belongs(value))
            return query
        else:
            return None

# =============================================================================
class S3SearchSkillsWidget(S3SearchOptionsWidget):
    """
        Options Widget to search for HRMs with specified Skills

        @ToDo: Provide a filter for confirmed/unconfirmed only
               (latter useful to see who needs confirming)

        @ToDo: Provide a filter for level of competency
               - meanwhile at least sort by level of competency
    """

    # -------------------------------------------------------------------------
    def widget(self, resource, vars):
        manager = current.manager
        c = manager.define_resource("hrm", "competency")
        return S3SearchOptionsWidget.widget(self, c, vars)

    # -------------------------------------------------------------------------
    @staticmethod
    def query(resource, value):
        if value:
            s3db = current.s3db
            htable = s3db.hrm_human_resource
            ptable = s3db.pr_person
            ctable = s3db.hrm_competency
            query = (htable.person_id == ptable.id) & \
                    (htable.deleted != True) & \
                    (ctable.person_id == ptable.id) & \
                    (ctable.deleted != True) & \
                    (ctable.skill_id.belongs(value))
            return query
        else:
            return None

# =============================================================================
class S3Search(S3CRUD):
    """
        RESTful Search Method for S3Resources
    """

    def __init__(self, simple=None, advanced=None, any=False, **args):
        """
            Constructor

            @param simple: the widgets for the simple search form as list
            @param advanced: the widgets for the advanced search form as list
            @param any: match "any of" (True) or "all of" (False) the options
                        in advanced search
        """

        S3CRUD.__init__(self)

        args = Storage(args)
        if simple is None:
            if "field" in args:
                if "name" in args:
                    name = args.name
                elif "_name" in args:
                    name = args._name
                else:
                    name = "search_simple"
                simple = S3SearchSimpleWidget(field=args.field,
                                              name=name,
                                              label=args.label,
                                              comment=args.comment)

        # Create a list of Simple search form widgets, by name,
        # and throw an error if a duplicate is found
        names = []
        self.simple = []
        if not isinstance(simple, (list, tuple)):
            simple = [simple]
        for widget in simple:
            if widget is not None:
                name = widget.attr._name
                if name in names:
                    raise SyntaxError("Duplicate widget: %s") % name
                # Widgets should be able to have default names
                # elif not name:
                #     raise SyntaxError("Widget with no name")
                else:
                    self.simple.append((name, widget))
                    names.append(name)

        # Create a list of Advanced search form widgets, by name,
        # and throw an error if a duplicate is found
        names = []
        self.advanced = []
        append = self.advanced.append
        if not isinstance(advanced, (list, tuple)):
            advanced = [advanced]
        for widget in advanced:
            if widget is not None:
                name = widget.attr._name
                if name in names:
                    raise SyntaxError("Duplicate widget: %s" % name)
                # Widgets should be able to have default names
                # elif not name:
                #    raise SyntaxError("Widget with no name")
                else:
                    append((name, widget))
                    names.append(name)

        self.__any = any

        if self.simple or self.advanced:
            self.__interactive = True
        else:
            self.__interactive = False

    # -------------------------------------------------------------------------
    def apply_method(self, r, **attr):
        """
            Entry point to apply search method to S3Requests

            @param r: the S3Request
            @param attr: request attributes
        """

        format = r.representation
        output = dict()

        if r.component and self != self.resource.search:
            output = self.resource.search(r, **attr)

        # Autocomplete-Widget
        elif "is_autocomplete" in attr:
            output = self.search_autocomplete(r, **attr)

        # Save search
        elif "save" in r.vars :
            r.interactive = False
            output = self.save_search(r, **attr)

        # Interactive or saved search
        elif "load" in r.vars or \
                r.interactive and self.__interactive:
            output = self.search_interactive(r, **attr)

        # SSPag response => CRUD native
        elif format == "aadata" and self.__interactive:
            output = self.select(r, **attr)

        # JSON search
        elif format == "json":
            output = self.search_json(r, **attr)

        # Autocomplete-JSON search
        elif format == "acjson":
            output = self.search_json_autocomplete(r, **attr)

        # Search form for popup on Map Layers
        elif format == "plain":
            output = self.search_interactive(r, **attr)

        # Not supported
        else:
            r.error(501, current.manager.ERROR.BAD_FORMAT)

        return output

    # -------------------------------------------------------------------------
    @staticmethod
    def _build_widget_query(resource, name, widget, form, query):
        """
            @todo: docstring
        """

        errors = None
        if hasattr(widget, "names"):
            value = Storage([(name, form.vars[name])
                             for name in widget.names
                             if name in form.vars])
        elif name in form.vars:
            value = form.vars[name]
        else:
            value = None
        if hasattr(widget, "validate"):
            errors = widget.validate(resource, value)

        if not errors:
            q = widget.query(resource, value)

            if q is not None:
                if query is None:
                    query = q
                else:
                    query = query & q

        return (query, errors)

    # -------------------------------------------------------------------------
    def save_search_widget(self, r, search_vars, **attr):
        """
            Add a widget to a Search form to allow saving this search to the
            user's profile, to which they can subscribe
        """

        T = current.T
        db = current.db
        request = self.request

        user_id = current.session.auth.user.id
        now = request.utcnow.microsecond
        save_search_btn_id = "save_my_filter_btn_%s" % now
        save_search_processing_id = "save_search_processing_%s" % now
        save_search_a_id = "save_search_a_%s" % now
        arg = "%s/save_search" % user_id
        save_search_a = DIV(T("View and Subscribe to Saved Searches"),
                            A(T("Here"),
                              _href=URL(r=request, c="pr", f="person",
                                        args=[arg]),
                              _target="_blank"
                             ),
                            ".",
                        _id=save_search_a_id,
                        _class="save_search_a"
                        )
        search_vars["prefix"] = r.controller
        search_vars["function"] = r.function

        table = current.s3db.pr_save_search
        if len(db(table.user_id == user_id).select(table.id,
                                                   limitby=(0, 1))):
            rows = db(table.user_id == user_id).select(table.ALL)
            import cPickle
            for row in rows:
                pat = "_"
                s_v = cPickle.loads(row.search_vars)
                if ((search_vars["prefix"] == s_v["prefix"]) and \
                    (search_vars["function"] == s_v["function"])):
                    s_dict = s_v["criteria"]
                    if "criteria" in search_vars:
                        c_dict = search_vars["criteria"]
                    else:
                        break
                    diff = [ k for k in c_dict if k not in s_dict ]
                    if not len(diff):
                        flag = 1
                        for j in s_dict.iterkeys():
                            if not re.match(pat, j):
                                if c_dict[j] != s_dict[j]:
                                    flag = 0
                                    break
                        if flag == 1:
                            return DIV(save_search_a,
                                       _style="font-size:12px; padding:5px 0px 5px 90px;",
                                       _id="save_search"
                                       )

        save_search_btn = A("Save Search",
                            _class="save_search_btn",
                            _id=save_search_btn_id,
                            _href="#",
                            _title=T("Save this search"))
        save_search_a["_style"] = "display:none;"
        save_search_processing = IMG(_src="/%s/static/img/ajax-loader.gif" % request.application,
                                    _id=save_search_processing_id,
                                    _class="save_search_processing_id",
                                    _style="display:none;"
                                    )
        s_var = {}
        s_var["save"] = True
        jurl = URL(r=request, c=r.controller, f=r.function,
                   args=["search"], vars=s_var)
        save_search_script = '''
$('#%s').live('click',function(){
 $('#%s').show()
 $('#%s').hide()
 $.ajax({
  url:'%s',
  data:'%s',
  success:function(data){
   $('#%s').show()
   $('#%s').hide()
  },
  type:'POST'
 })
 return false
})
''' % (save_search_btn_id,
       save_search_processing_id,
       save_search_btn_id,
       jurl,
       json.dumps(search_vars),
       save_search_a_id,
       save_search_processing_id)

        current.response.s3.jquery_ready.append(save_search_script)

        widget = DIV(save_search_processing,
                    save_search_a,
                    save_search_btn,
                    _style="font-size:12px; padding:5px 0px 5px 90px;",
                    _id="save_search"
                    )
        return widget

    # -------------------------------------------------------------------------
    def search_interactive(self, r, **attr):
        """
            Interactive search

            @param r: the S3Request instance
            @param attr: request parameters
        """

        # Get environment
        T = current.T
        session = current.session
        request = self.request
        response = current.response
        s3 = response.s3
        resource = self.resource
        settings = current.deployment_settings
        db = current.db
        s3db = current.s3db
        gis = current.gis
        table = self.table
        tablename = self.tablename

        # Get representation
        representation = r.representation

        # Initialize output
        output = dict()

        # Get table-specific parameters
        config = self._config
        sortby = config("sortby", [[1, "asc"]])
        orderby = config("orderby", None)
        list_fields = config("list_fields")
        insertable = config("insertable", True)

        # Initialize the form
        form = DIV(_class="search_form form-container")

        # Figure out which set of form values to use
        # POST > GET > session > unfiltered
        if r.http == "POST":
            # POST
            form_values = r.post_vars
        else:
            url_options = Storage([(k, v) for k, v in r.get_vars.iteritems() if v])
            if url_options:
                # GET
                form_values = url_options
            else:
                session_options = session.s3.search_options
                if session_options and tablename in session_options:
                    # session
                    session_options = session_options[tablename]
                else:
                    # unfiltered
                    session_options = Storage()
                form_values = session_options

        # Build the search forms
        simple_form, advanced_form = self.build_forms(r, form_values)

        # Check for Load Search
        if "load" in r.get_vars:
            search_id = r.get_vars.get("load", None)
            if not search_id:
                r.error(400, current.manager.ERROR.BAD_RECORD)
            r.post_vars = r.vars
            search_table = s3db.pr_save_search
            _query = (search_table.id == search_id)
            record = db(_query).select(record.search_vars,
                                       limitby=(0, 1)).first()
            if not record:
                r.error(400, current.manager.ERROR.BAD_RECORD)
            import cPickle
            s_vars = cPickle.loads(record.search_vars)
            r.post_vars = Storage(s_vars["criteria"])
            r.http = "POST"

        # Process the search forms
        query, errors = self.process_forms(r,
                                           simple_form,
                                           advanced_form,
                                           form_values)
        if not errors:
            resource.add_filter(query)
            search_vars = dict(simple=False,
                               advanced=True,
                               criteria=form_values)
        else:
            search_vars = dict()

        if representation == "plain":
            # Map popup filter
            # Return just the advanced form, no results
            form.append(advanced_form)
            output["item"] = form
            response.view = self._view(r, "plain.html")
            return output

        if s3.simple_search:
            form.append(DIV(_id="search-mode", _mode="simple"))
        else:
            form.append(DIV(_id="search-mode", _mode="advanced"))

        # Save Search Widget
        if session.auth and settings.get_save_search_widget():
            save_search = self.save_search_widget(r, search_vars, **attr)
        else:
            save_search = DIV()

        # Complete the output form-DIV()
        if simple_form is not None:
            simple_form.append(save_search)
            form.append(simple_form)
        if advanced_form is not None:
            advanced_form.append(save_search)
            form.append(advanced_form)
        output["form"] = form

        # Build session filter (for SSPag)
        if not s3.no_sspag:
            limit = 1
            ids = resource.get_id()
            if ids:
                if not isinstance(ids, list):
                    ids = str(ids)
                else:
                    ids = ",".join([str(i) for i in ids])
                session.s3.filter = {"%s.id" % resource.name: ids}
        else:
            limit = None

        # List fields
        linkto = self._linkto(r)
        if not list_fields:
            fields = resource.readable_fields()
            list_fields = [f.name for f in fields]
        else:
            fields = [table[f] for f in list_fields if f in table.fields]
        if not fields:
            fields = []
        if fields[0].name != table.fields[0]:
            fields.insert(0, table[table.fields[0]])
        if list_fields[0] != table.fields[0]:
            list_fields.insert(0, table.fields[0])
        if not orderby:
            orderby = fields[0]

        # Truncate long texts
        if r.interactive or representation == "aadata":
            for f in table:
                if str(f.type) == "text" and not f.represent:
                    f.represent = self.truncate

        # Get the result table
        items = resource.sqltable(fields=list_fields,
                                  limit=limit,
                                  orderby=orderby,
                                  distinct=True,
                                  linkto=linkto,
                                  download_url=self.download_url,
                                  format=representation)

        # Remove the dataTables search box to avoid confusion
        s3.dataTable_NobFilter = True

        if items:
            if not s3.no_sspag:
                # Pre-populate SSPag cache (avoids the 1st Ajax request)
                totalrows = resource.count(distinct=True)
                if totalrows:
                    if s3.dataTable_iDisplayLength:
                        limit = 2 * s3.dataTable_iDisplayLength
                    else:
                        limit = 50
                    sqltable = resource.sqltable(fields=list_fields,
                                                 start=0,
                                                 limit=limit,
                                                 orderby=orderby,
                                                 distinct=True,
                                                 linkto=linkto,
                                                 download_url=self.download_url,
                                                 as_page=True,
                                                 format=representation)

                    aadata = dict(aaData=sqltable or [])
                    aadata.update(iTotalRecords=totalrows,
                                  iTotalDisplayRecords=totalrows)
                    response.aadata = jsons(aadata)
                    s3.start = 0
                    s3.limit = limit

        elif not items:
            items = self.crud_string(tablename, "msg_no_match")

        output["items"] = items
        output["sortby"] = sortby

        if isinstance(items, DIV):
            filter = session.s3.filter
            app = request.application
            list_formats = DIV(T("Export to:"),
                               A(IMG(_src="/%s/static/img/pdficon_small.gif" % app),
                                 _title=T("Export in PDF format"),
                                 _href=r.url(method="", representation="pdf",
                                             vars=filter)),
                               A(IMG(_src="/%s/static/img/icon-xls.png" % app),
                                 _title=T("Export in XLS format"),
                                 _href=r.url(method="", representation="xls",
                                             vars=filter)),
                               A(IMG(_src="/%s/static/img/RSS_16.png" % app),
                                 _title=T("Export in RSS format"),
                                 _href=r.url(method="", representation="rss",
                                             vars=filter)),
                               _id="list_formats")
            tabs = []

            if "location_id" in table or \
               "site_id" in table:
                # Add a map for search results
                # (this same map is also used by the Map Search Widget, if-present)
                tabs.append((T("Map"), "map"))
                app = request.application
                list_formats.append(A(IMG(_src="/%s/static/img/kml_icon.png" % app),
                                      _title=T("Export in KML format"),
                                      _href=r.url(method="",
                                                  representation="kml",
                                                  vars=filter)),
                                    )
                # Build URL to load the features onto the map
                if query:
                    vars = query.serialize_url(resource=resource)
                else:
                    vars = None
                url = URL(extension="geojson",
                          args=None,
                          vars=vars)
                feature_resources = [{
                        "name"   : T("Search Results"),
                        "id"     : "search_results",
                        "url"    : url,
                        "active" : False, # Gets activated when the Map is opened up
                        "marker" : gis.get_marker(request.controller, request.function)
                    }]
                map_popup = gis.show_map(
                                        feature_resources=feature_resources,
                                        # Added by search widget onClick in s3.dataTables.js
                                        #add_polygon = True,
                                        #add_polygon_active = True,
                                        catalogue_layers=True,
                                        legend=True,
                                        toolbar=True,
                                        collapsed=True,
                                        #search = True,
                                        window=True,
                                        window_hide=True
                                        )
                s3.dataTableMap = map_popup

            if settings.has_module("msg") and \
               ("pe_id" in table or "person_id" in table):
                # Provide the ability to Message person entities in search results
                tabs.append((T("Message"), "compose"))

            if tabs:
                tabs.insert(0, ((T("List"), None)))
        else:
            list_formats = ""
            tabs = []


        # Search Tabs
        search_tabs = s3_search_tabs(r, tabs)
        output["search_tabs"] = search_tabs

        # List Formats
        output["list_formats"] = list_formats

        # Title and subtitle
        output["title"] = self.crud_string(tablename, "title_search")
        output["subtitle"] = self.crud_string(tablename, "msg_match")

        # View
        response.view = self._view(r, "search.html")

        # RHeader gets added later in S3Method()

        return output

    # -------------------------------------------------------------------------
    def process_forms(self, r, simple_form, advanced_form, form_values):
        """
            Validate the form values against the forms. If valid, generate
            and return a query object. Otherwise return an empty query and
            the errors.

            If valid, save the values into the users' session.
        """

        s3 = current.session.s3

        query = None
        errors = None

        # Create a container in the session to saves search options
        if "search_options" not in s3:
            s3.search_options = Storage()

        # Process the simple search form:
        simple = simple_form is not None
        if simple_form is not None:
            if simple_form.accepts(form_values,
                                   formname="search_simple",
                                   keepvalues=True):
                for name, widget in self.simple:
                    query, errors = self._build_widget_query(self.resource,
                                                             name,
                                                             widget,
                                                             simple_form,
                                                             query)
                    if errors:
                        simple_form.errors.update(errors)
                errors = simple_form.errors

                # Save the form values into the session
                s3.search_options[self.tablename] = \
                    Storage([(k, v) for k, v in form_values.iteritems() if v])
            elif simple_form.errors:
                errors = simple_form.errors
                return query, errors, simple

        # Process the advanced search form:
        if advanced_form is not None:
            if advanced_form.accepts(form_values,
                                     formname="search_advanced",
                                     keepvalues=True):
                simple = False
                for name, widget in self.advanced:
                    query, errors = self._build_widget_query(self.resource,
                                                             name,
                                                             widget,
                                                             advanced_form,
                                                             query)
                    if errors:
                        advanced_form.errors.update(errors)

                errors = advanced_form.errors

                # Save the form values into the session
                s3.search_options[self.tablename] = \
                    Storage([(k, v) for k, v in form_values.iteritems() if v])
            elif advanced_form.errors:
                simple = False

        current.response.s3.simple_search = simple

        return (query, errors)

    # -------------------------------------------------------------------------
    def build_forms(self, r, form_values=None):
        """
            Builds a form customised to the module/resource. Includes a link
            to the create form for this resource.
        """

        simple = self.simple
        advanced = self.advanced

        T = current.T
        tablename = self.tablename
        representation = r.representation

        simple_form = None
        advanced_form = None

        # Add-link (common to all forms)
        ADD = self.crud_string(tablename, "label_create_button")
        href_add = r.url(method="create", representation=representation)
        insertable = self._config("insertable", True)
        authorised = self.permit("create", tablename)
        if authorised and insertable and representation != "plain":
            add_link = self.crud_button(ADD, _href=href_add,
                                        _id="add-btn", _class="action-lnk")
        else:
            add_link = ""

        # Simple search form
        if simple:
            # Switch-link
            if advanced:
                switch_link = A(T("Advanced Search"), _href="#",
                                _class="action-lnk advanced-lnk")
            else:
                switch_link = ""
            simple_form = self._build_form(simple,
                                           form_values=form_values,
                                           add=add_link,
                                           switch=switch_link,
                                           _class="simple-form")

        # Advanced search form
        if advanced:
            if simple and not r.representation == "plain":
                switch_link = A(T("Simple Search"), _href="#",
                                _class="action-lnk simple-lnk")
                _class = "%s hide"
            else:
                switch_link = ""
                _class = "%s"
            advanced_form = self._build_form(advanced,
                                             form_values=form_values,
                                             add=add_link,
                                             switch=switch_link,
                                             _class=_class % "advanced-form")

        return (simple_form, advanced_form)

    # -------------------------------------------------------------------------
    def _build_form(self, widgets, form_values=None, add="", switch="", **attr):
        """
            @todo: docstring
        """

        T = current.T
        request = self.request
        resource = self.resource

        trows = []
        for name, widget in widgets:

            _widget = widget.widget(resource, form_values)
            if _widget is None:
                # Skip this widget as we have nothing but the label
                continue

            label = widget.field
            if isinstance(label, (list, tuple)) and len(label):
                label = label[0]
            comment = ""

            if hasattr(widget, "attr"):
                label = widget.attr.get("label", label)
                comment = widget.attr.get("comment", comment)

            tr = TR(TD("%s: " % label, _class="w2p_fl"), _widget)

            if comment:
                tr.append(DIV(DIV(_class="tooltip",
                                  _title="%s|%s" % (label, comment))))
            trows.append(tr)

        trows.append(TR("", TD(INPUT(_type="submit", _value=T("Search")),
                               switch, add)))
        form = FORM(TABLE(trows), **attr)
        return form

    # -------------------------------------------------------------------------
    def search_json(self, r, **attr):
        """
            JSON search method for S3AutocompleteWidget

            @param r: the S3Request
            @param attr: request attributes
        """

        output = None

        _vars = self.request.vars

        # JQueryUI Autocomplete uses "term" instead of "value"
        # (old JQuery Autocomplete uses "q" instead of "value")
        value = _vars.value or _vars.term or _vars.q or None

        # We want to do case-insensitive searches
        # (default anyway on MySQL/SQLite, but not PostgreSQL)
        value = value.lower().strip()

        if _vars.field and _vars.filter and value:
            s3db = current.s3db
            resource = self.resource
            table = self.table

            limit = int(_vars.limit or 0)

            fieldname = str.lower(_vars.field)
            field = table[fieldname]

            # Default fields to return
            fields = [table.id, field]
            if self.tablename == "org_site":
                # Simpler to provide an exception case than write a whole new class
                table = s3db.org_site
                fields.append(table.instance_type)

            filter = _vars.filter
            if filter == "~":
                # Normal single-field Autocomplete
                query = (field.lower().like(value + "%"))

            elif filter == "=":
                if field.type.split(" ")[0] in \
                    ["reference", "id", "float", "integer"]:
                    # Numeric, e.g. Organizations' offices_by_org
                    query = (field == value)
                else:
                    # Text
                    query = (field.lower() == value)

            elif filter == "<":
                query = (field < value)

            elif filter == ">":
                query = (field > value)

            else:
                output = current.manager.xml.json_message(
                            False,
                            400,
                            "Unsupported filter! Supported filters: ~, =, <, >")
                raise HTTP(400, body=output)

            # Exclude records which are already linked:
            #      ?link=<linktablename>.<leftkey>.<id>.<rkey>.<fkey>
            # e.g. ?link=project_organisation.organisation_id.5.project_id.id
            if "link" in _vars:
                try:
                    link, lkey, _id, rkey, fkey = _vars.link.split(".")
                    linktable = s3db[link]
                    fq = (linktable[rkey] == table[fkey]) & \
                         (linktable[lkey] == _id)
                    linked = current.db(fq).select(table._id)
                    exclude = (~(table._id.belongs([r[table._id.name]
                                                    for r in linked])))
                except Exception, e:
                    pass # ignore
                else:
                    query &= exclude

            # Select only or exclude template records:
            # to only select templates:
            #           ?template=<fieldname>.<value>,
            #      e.g. ?template=template.true
            # to exclude templates:
            #           ?template=~<fieldname>.<value>
            #      e.g. ?template=~template.true
            if "template" in _vars:
                try:
                    flag, val = _vars.template.split(".", 1)
                    if flag[0] == "~":
                        exclude = True
                        flag = flag[1:]
                    else:
                        exclude = False
                    ffield = table[flag]
                except:
                    pass # ignore
                else:
                    if str(ffield.type) == "boolean":
                        if val.lower() == "true":
                            val = True
                        else:
                            val = False
                    if exclude:
                        templates = (ffield != val)
                    else:
                        templates = (ffield == val)
                    resource.add_filter(templates)

            resource.add_filter(query)

            if filter == "~":
                if (not limit or limit > MAX_SEARCH_RESULTS) and \
                   resource.count() > MAX_SEARCH_RESULTS:
                    output = jsons([dict(id="",
                                         name="Search results are over %d. Please input more characters." \
                                         % MAX_SEARCH_RESULTS)])

            if output is None:
                output = resource.exporter.json(resource,
                                                start=0,
                                                limit=limit,
                                                fields=fields,
                                                orderby=field)
            current.response.headers["Content-Type"] = "application/json"

        else:
            output = current.manager.xml.json_message(
                        False,
                        400,
                        "Missing options! Require: field, filter & value")
            raise HTTP(400, body=output)

        return output

    # -------------------------------------------------------------------------
    @staticmethod
    def _check_search_autcomplete_search_simple_widget(widget):
        """
            @todo: docstring
        """
        if not isinstance(widget, S3SearchSimpleWidget):
            raise SyntaxError("First simple widget for Search AutoComplete must be S3SearchSimpleWidget")

    # -------------------------------------------------------------------------
    def search_autocomplete(self, r, **attr):
        """
            Interactive search

            @param r: the S3Request instance
            @param attr: request parameters
        """

        # Get environment
        T = current.T
        resource = self.resource
        vars = self.request.get_vars

        resource.clear_query()

        # Fieldname of the value for the autocomplete (default to id)
        get_fieldname = attr.get("get_fieldname")
        fieldname = attr.get("fieldname")
        value = attr.get("value")

        # Get representation
        representation = r.representation

        # Initialize output
        feature_queries = []
        bounds = None
        output = dict()
        simple = False

        # Get table-specific parameters
        sortby = self._config("sortby", [[1, "asc"]])
        orderby = self._config("orderby", None)
        list_fields = self._config("list_fields")
        insertable = self._config("insertable", True)

        # Initialize the form
        form_attr = dict(_class="search_form form-container",
                         _prefix=resource.prefix,
                         _resourcename=resource.name,
                         _fieldname=fieldname,
                         )
        if get_fieldname:
            form_attr["_get_fieldname"] = get_fieldname
            # Otherwise default get_fieldname is "id"

        form = DIV(**form_attr)

        # Append the simple search form
        if self.simple:
            simple = True
            if self.advanced:
                switch_link = A(T("Advanced Search"), _href="#",
                                _class="action-lnk advanced-lnk %s",
                                _fieldname=fieldname)
            else:
                switch_link = ""
            # Only display the S3SearchSimpleWidget (should be first)
            name, widget = self.simple[0]

            self._check_search_autcomplete_search_simple_widget(widget)
            name = "%s_search_simple_simple" % fieldname

            autocomplete_widget = widget.widget(resource,
                                                 vars,
                                                 name=name,
                                                 value=value,
                                                 autocomplete="off")

            simple_form = DIV(TABLE(autocomplete_widget,
                                      switch_link
                                     ),
                              _class="simple-form")
            form.append(simple_form)

        # Append the advanced search form
        if self.advanced:
            trows = []
            first_widget = True
            for name, widget in self.advanced:
                _widget = widget.widget(resource, vars)
                if _widget is None:
                    # Skip this widget as we have nothing but the label
                    continue
                label = widget.field
                if first_widget:
                    self._check_search_autcomplete_search_simple_widget(widget)
                    name = "%s_search_simple_advanced" % fieldname
                    autocomplete_widget = widget.widget(resource,
                                                         vars,
                                                         name=name,
                                                         value=value,
                                                         autocomplete="off")
                    first_widget = False
                else:
                    if isinstance(label, (list, tuple)) and len(label):
                        label = label[0]
                    if hasattr(widget, "attr"):
                        label = widget.attr.get("label", label)
                    tr = TR(TD("%s: " % label, _class="w2p_fl"), _widget)
                    trows.append(tr)

            if self.simple:
                switch_link = A(T("Simple Search"), _href="#",
                                _class="action-lnk simple-lnk",
                                _fieldname=fieldname)
            else:
                switch_link = ""

            if simple:
                _class = "hide"
            else:
                _class = None
            advanced_form = DIV(autocomplete_widget,
                                TABLE(trows),
                                TABLE(TR(switch_link)),
                                _class="%s advanced-form" % _class,
                                #_resourcename = resource.name
                                )
            form.append(advanced_form)

        output.update(form=form)
        return output

    # -------------------------------------------------------------------------
    def search_json_autocomplete(self, r, **attr):
        """
            @todo: docstring
        """

        query = None
        errors = True

        request = self.request
        resource = self.resource
        response = current.response

        response.headers["Content-Type"] = "application/json"

        # Process the simple search form:
        if self.simple and request.vars.simple_form:
            for name, widget in self.simple:
                # Pass request instead of form - it contains the vars
                query, errors = self._build_widget_query(resource,
                                                         name,
                                                         widget,
                                                         request,
                                                         query)
                if errors:
                    break
        # Process the advanced search form:
        elif self.advanced:
            for name, widget in self.advanced:
                # Pass request instead of form - it contains the vars
                query, errors = self._build_widget_query(resource,
                                                         name,
                                                         widget,
                                                         request,
                                                         query)
                if errors:
                    break
        else:
            errors = True

        resource.add_filter(query)
        try:
            get_fieldname = request.vars.get("get_fieldname", "id")
            field = resource.table[get_fieldname]
        except:
            errors = True

        # How can this be done more elegantly?
        resource_represent = { "human_resource":
                               lambda id: \
                                response.s3.hrm_human_resource_represent(id,
                                                                         show_link=True)
                              }
        if get_fieldname == "id":
            represent = resource_represent[resource.name]
        else:
            represent = field.represent

        attributes = dict(orderby=field,
                          limitby=resource.limitby(start=0, limit=11),
                          distinct=True)

        # Get the rows
        rows = resource.select(field, **attributes)

        if not errors:
            output = [{ "id"   : row[get_fieldname],
                       "represent" : str(represent(row[get_fieldname]))
                       } for row in rows ]
        else:
            jsons("{}")

        return jsons(output)

    # -------------------------------------------------------------------------
    @staticmethod
    def save_search(r, **attr):
        """
            Save a Search Filter in the user's profile
            - db.pr_save_search
        """

        search_vars = json.load(r.body)
        s_vars = {}

        for i in search_vars.iterkeys():
            if str(i) == "criteria" :
                s_dict = {}
                c_dict = search_vars[i]
                for j in c_dict.iterkeys():
                    key = str(j)
                    s_dict[key] = str(c_dict[j])
                s_vars[str(i)] = s_dict
            else:
                key = str(i)
                s_vars[key] = str(search_vars[i])

        import cPickle
        search_str = cPickle.dumps(s_vars)
        table = current.s3db.pr_save_search
        query = (table.user_id == current.auth.user_id) & \
                (table.search_vars == search_str)
        if len(current.db(query).select(table.id)) == 0:
            new_search = {}
            new_search["search_vars"] = search_str
            _id = table.insert(**new_search)
        msg = "success"
        return msg

# =============================================================================
class S3LocationSearch(S3Search):
    """
        Search method with specifics for Location records (hierarchy search)
    """

    def search_json(self, r, **attr):
        """
            JSON search method for S3LocationAutocompleteWidget

            @param r: the S3Request
            @param attr: request attributes
        """

        output = None
        response = current.response
        resource = self.resource
        table = self.table

        # Query comes in pre-filtered to accessible & deletion_status
        # Respect response.s3.filter
        resource.add_filter(response.s3.filter)

        _vars = self.request.vars

        limit = int(_vars.limit or 0)

        # JQueryUI Autocomplete uses "term" instead of "value"
        # (old JQuery Autocomplete uses "q" instead of "value")
        value = _vars.value or _vars.term or _vars.q or None

        # We want to do case-insensitive searches
        # (default anyway on MySQL/SQLite, but not PostgreSQL)
        if value:
            value = value.lower().strip()

        query = None
        fields = []
        field = table.id

        if _vars.field and _vars.filter and value:
            fieldname = str.lower(_vars.field)
            field = table[fieldname]

            # Default fields to return
            fields = [table.id,
                      table.name,
                      table.level,
                      table.parent,
                      table.path,
                      table.uuid,
                      table.lat,
                      table.lon,
                      table.addr_street,
                      table.addr_postcode]

            # Optional fields
            if "level" in _vars and _vars.level:
                if _vars.level == "null":
                    level = None
                elif "|" in _vars.level:
                    level = _vars.level.split("|")
                else:
                    level = str.upper(_vars.level)
            else:
                level = None

            if "parent" in _vars and _vars.parent:
                if _vars.parent == "null":
                    parent = None
                else:
                    parent = int(_vars.parent)
            else:
                parent = None

            if "children" in _vars and _vars.children:
                if _vars.children == "null":
                    children = None
                else:
                    children = int(_vars.children)
            else:
                children = None

            if "field2" in _vars and _vars.field2:
                fieldname = str.lower(_vars.field2)
                field2 = table[fieldname]
            else:
                field2 = None

            if "exclude_field" in _vars:
                exclude_field = str.lower(_vars.exclude_field)
                if "exclude_value" in _vars:
                    exclude_value = str.lower(_vars.exclude_value)
                else:
                    exclude_value = None
            else:
                exclude_field = None
                exclude_value = None

            filter = _vars.filter
            if filter == "~":
                if children:
                    # New LocationSelector
                    children = current.gis.get_children(children, level=level)
                    children = children.find(lambda row: \
                                             row.name and value in str.lower(row.name))
                    output = children.json()
                    response.headers["Content-Type"] = "application/json"
                    return output

                if exclude_field and exclude_value:
                    # Old LocationSelector
                    # Filter out poor-quality data, such as from Ushahidi
                    query = (field.lower().like(value + "%")) & \
                            ((table[exclude_field].lower() != exclude_value) | \
                             (table[exclude_field] == None))

                elif field2:
                    # New LocationSelector
                    query = ((field.lower().like(value + "%")) | \
                             (field2.lower().like(value + "%")))

                else:
                    # Normal single-field
                    query = (field.lower().like(value + "%"))

                if level:
                    resource.add_filter(query)
                    # New LocationSelector or Autocomplete
                    if isinstance(level, list):
                        query = (table.level.belongs(level))
                    elif str.upper(level) == "NULLNONE":
                        level = None
                        query = (table.level == level)
                    else:
                        query = (table.level == level)

                if parent:
                    # New LocationSelector
                    resource.add_filter(query)
                    query = (table.parent == parent)

            elif filter == "=":
                if field.type.split(" ")[0] in \
                   ["reference", "id", "float", "integer"]:
                    # Numeric, e.g. Organizations' offices_by_org
                    query = (field == value)
                else:
                    # Text
                    if value == "nullnone":
                        # i.e. old Location Selector
                        query = (field == None)
                    else:
                        query = (field.lower() == value)

                if parent:
                    # i.e. gis_location hierarchical search
                    resource.add_filter(query)
                    query = (table.parent == parent)

                fields = [table.id,
                          table.name,
                          table.level,
                          table.uuid,
                          table.parent,
                          table.lat,
                          table.lon,
                          table.addr_street,
                          table.addr_postcode]
            else:
                output = current.manager.xml.json_message(
                                False,
                                400,
                                "Unsupported filter! Supported filters: ~, ="
                            )
                raise HTTP(400, body=output)


        if not fields:
            append = fields.append
            for field in table.fields:
                append(table[field])

        resource.add_filter(query)

        if filter == "~":
            if (not limit or limit > MAX_SEARCH_RESULTS) and resource.count() > MAX_SEARCH_RESULTS:
                output = jsons([dict(id="",
                                     name="Search results are over %d. Please input more characters." \
                                     % MAX_SEARCH_RESULTS)])
        elif not parent:
            if (not limit or limit > MAX_RESULTS) and resource.count() > MAX_RESULTS:
                output = jsons([])

        if output is None:
            output = resource.exporter.json(resource,
                                            start=0,
                                            limit=limit,
                                            fields=fields,
                                            orderby=field)

        response.headers["Content-Type"] = "application/json"
        return output

# =============================================================================
class S3OrganisationSearch(S3Search):
    """
        Search method with specifics for Organisation records
        - searches name & acronym for both this organisation & the parent of
          branches
    """

    def search_json(self, r, **attr):
        """
            JSON search method for S3OrganisationAutocompleteWidget

            @param r: the S3Request
            @param attr: request attributes
        """

        response = current.response
        resource = self.resource
        table = self.table

        # Query comes in pre-filtered to accessible & deletion_status
        # Respect response.s3.filter
        resource.add_filter(response.s3.filter)

        _vars = self.request.vars # should be request.get_vars?

        # JQueryUI Autocomplete uses "term" instead of "value"
        # (old JQuery Autocomplete uses "q" instead of "value")
        value = _vars.value or _vars.term or _vars.q or None

        # We want to do case-insensitive searches
        # (default anyway on MySQL/SQLite, but not PostgreSQL)
        value = value.lower().strip()

        filter = _vars.filter
        limit = int(_vars.limit or 0)

        if filter and value:

            btable = current.s3db.org_organisation_branch
            field = table.name
            field2 = table.acronym
            field3 = btable.organisation_id

            # Fields to return
            fields = [table.id, field, field2, field3]

            if filter == "~":
                query = (S3FieldSelector("parent.name").lower().like(value + "%")) | \
                        (S3FieldSelector("parent.acronym").lower().like(value + "%")) | \
                        (S3FieldSelector("organisation.name").lower().like(value + "%")) | \
                        (S3FieldSelector("organisation.acronym").lower().like(value + "%"))

            else:
                output = current.manager.xml.json_message(
                                False,
                                400,
                                "Unsupported filter! Supported filters: ~"
                            )
                raise HTTP(400, body=output)

        resource.add_filter(query)

        if (not limit or limit > MAX_SEARCH_RESULTS) and resource.count() > MAX_SEARCH_RESULTS:
            output = jsons([dict(id="",
                                 name="Search results are over %d. Please input more characters." \
                                 % MAX_SEARCH_RESULTS)])
        else:
            attributes = dict(orderby=field)
            limitby = resource.limitby(start=0, limit=limit)
            if limitby is not None:
                attributes["limitby"] = limitby
            rows = resource.select(*fields, **attributes)
            output = []
            append = output.append
            db = current.db
            for row in rows:
                name = row[table].name
                parent = None
                if "org_organisation_branch" in row:
                    query = (table.id == row[btable].organisation_id)
                    parent = db(query).select(table.name,
                                              limitby = (0, 1)).first()
                    if parent:
                        name = "%s > %s" % (parent.name,
                                            name)
                if not parent:
                    acronym = row[table].acronym
                    if acronym:
                        name = "%s (%s)" % (name,
                                            acronym)
                record = dict(
                    id = row[table].id,
                    name = name,
                    )
                append(record)
            output = jsons(output)

        response.headers["Content-Type"] = "application/json"
        return output

# =============================================================================
class S3PersonSearch(S3Search):
    """
        Search method with specifics for Person records (full name search)
    """

    def search_json(self, r, **attr):
        """
            JSON search method for S3PersonAutocompleteWidget

            @param r: the S3Request
            @param attr: request attributes
        """

        response = current.response
        resource = self.resource

        # Query comes in pre-filtered to accessible & deletion_status
        # Respect response.s3.filter
        resource.add_filter(response.s3.filter)

        _vars = self.request.vars # should be request.get_vars?

        # JQueryUI Autocomplete uses "term" instead of "value"
        # (old JQuery Autocomplete uses "q" instead of "value")
        value = _vars.value or _vars.term or _vars.q or None

        # We want to do case-insensitive searches
        # (default anyway on MySQL/SQLite, but not PostgreSQL)
        value = value.lower()

        filter = _vars.filter
        limit = int(_vars.limit or 0)

        if filter and value:
            table = self.table
            field = table.first_name
            field2 = table.middle_name
            field3 = table.last_name

            # Fields to return
            fields = [table.id, field, field2, field3]

            if filter == "~":
                # pr_person Autocomplete
                if " " in value:
                    value1, value2 = value.split(" ", 1)
                    value2 = value2.strip()
                    query = (field.lower().like(value1 + "%")) & \
                            ((field2.lower().like(value2 + "%")) | \
                             (field3.lower().like(value2 + "%")))
                else:
                    value = value.strip()
                    query = ((field.lower().like(value + "%")) | \
                            (field2.lower().like(value + "%")) | \
                            (field3.lower().like(value + "%")))

            else:
                output = current.manager.xml.json_message(
                                False,
                                400,
                                "Unsupported filter! Supported filters: ~"
                            )
                raise HTTP(400, body=output)

        resource.add_filter(query)

        if (not limit or limit > MAX_SEARCH_RESULTS) and resource.count() > MAX_SEARCH_RESULTS:
            output = jsons([dict(id="",
                                 name="Search results are over %d. Please input more characters." \
                                     % MAX_SEARCH_RESULTS)])
        else:
            output = resource.exporter.json(resource,
                                            start=0,
                                            limit=limit,
                                            fields=fields,
                                            orderby=field)

        response.headers["Content-Type"] = "application/json"
        return output

# =============================================================================
class S3HRSearch(S3Search):
    """
        Search method with specifics for HRM records
            - full name search
            - include Organisation & Job Role in the output
    """

    def search_json(self, r, **attr):
        """
            JSON search method for S3HumanResourceAutocompleteWidget

            @param r: the S3Request
            @param attr: request attributes
        """

        response = current.response
        resource = self.resource

        # Query comes in pre-filtered to accessible & deletion_status
        # Respect response.s3.filter
        resource.add_filter(response.s3.filter)

        _vars = self.request.vars # should be request.get_vars?

        # JQueryUI Autocomplete uses "term" instead of "value"
        # (old JQuery Autocomplete uses "q" instead of "value")
        value = _vars.value or _vars.term or _vars.q or None

        # We want to do case-insensitive searches
        # (default anyway on MySQL/SQLite, but not PostgreSQL)
        value = value.lower()

        filter = _vars.filter
        limit = int(_vars.limit or 0)

        if filter and value:
            table = self.table
            s3db = current.s3db
            ptable = s3db.pr_person
            field = ptable.first_name
            field2 = ptable.middle_name
            field3 = ptable.last_name
            otable = s3db.org_organisation
            #jtable = s3db.hrm_job_role

            # Fields to return
            fields = [table.id,
                      otable.name,
                      #jtable.name,
                      field,
                      field2,
                      field3
                      ]

            if filter == "~":
                # Autocomplete
                #left = jtable.on(table.job_role_id == jtable.id)
                if " " in value:
                    value1, value2 = value.split(" ", 1)
                    value2 = value2.strip()
                    query = (table.organisation_id == otable.id) & \
                            (table.person_id == ptable.id) & \
                            ((field.lower().like(value1 + "%")) & \
                            ((field2.lower().like(value2 + "%")) | \
                             (field3.lower().like(value2 + "%"))))
                else:
                    value = value.strip()
                    query = (S3FieldSelector("organisation.id") == S3FieldSelector("human_resource.organisation_id")) & \
                            (S3FieldSelector("person.id") == S3FieldSelector("human_resource.person_id")) & \
                            ((S3FieldSelector("person.first_name").lower().like(value + "%")) | \
                             (S3FieldSelector("person.middle_name").lower().like(value + "%")) | \
                             (S3FieldSelector("person.last_name").lower().like(value + "%")))

            else:
                output = current.manager.xml.json_message(
                                False,
                                400,
                                "Unsupported filter! Supported filters: ~"
                            )
                raise HTTP(400, body=output)

        resource.add_filter(query)

        if (not limit or limit > MAX_SEARCH_RESULTS) and resource.count() > MAX_SEARCH_RESULTS:
            output = jsons([dict(id="",
                                 name="Search results are over %d. Please input more characters." \
                                 % MAX_SEARCH_RESULTS)])
        else:
            output = resource.exporter.json(resource,
                                            start=0,
                                            limit=limit,
                                            fields=fields,
                                            orderby=field)

        response.headers["Content-Type"] = "application/json"
        return output

# =============================================================================
class S3PentitySearch(S3Search):
    """
        Search method with specifics for Pentity records (full name search)
    """

    def search_json(self, r, **attr):
        """
            Legacy JSON search method (for autocomplete widgets)

            @param r: the S3Request
            @param attr: request attributes
        """

        response = current.response
        resource = self.resource
        table = self.table
        s3db = current.s3db

        # Query comes in pre-filtered to accessible & deletion_status
        # Respect response.s3.filter
        resource.add_filter(response.s3.filter)

        _vars = self.request.vars # should be request.get_vars?

        # JQueryUI Autocomplete uses "term" instead of "value"
        # (old JQuery Autocomplete uses "q" instead of "value")
        value = _vars.value or _vars.term or _vars.q or None

        # We want to do case-insensitive searches
        # (default anyway on MySQL/SQLite, but not PostgreSQL)
        value = value.lower()

        filter = _vars.filter
        limit = int(_vars.limit or 0)

        # Persons
        if filter and value:
            ptable = s3db.pr_person
            field = ptable.first_name
            field2 = ptable.middle_name
            field3 = ptable.last_name

            if filter == "~":
                # pr_person Autocomplete
                if " " in value:
                    value1, value2 = value.split(" ", 1)
                    value2 = value2.strip()
                    query = (field.lower().like(value1 + "%")) & \
                            (field2.lower().like(value2 + "%")) | \
                            (field3.lower().like(value2 + "%"))
                else:
                    value = value.strip()
                    query = ((field.lower().like(value + "%")) | \
                            (field2.lower().like(value + "%")) | \
                            (field3.lower().like(value + "%")))
                resource.add_filter(query)
            else:
                output = current.manager.xml.json_message(
                                False,
                                400,
                                "Unsupported filter! Supported filters: ~"
                            )
                raise HTTP(400, body=output)

        resource.add_filter(ptable.pe_id == table.pe_id)

        output = resource.exporter.json(resource, start=0, limit=limit,
                                        fields=[table.pe_id], orderby=field)
        items = json.loads(output)

        # Add Groups
        if filter and value:
            gtable = s3db.pr_group
            field = gtable.name
            query = field.lower().like("%" + value + "%")
            resource.clear_query()
            resource.add_filter(query)
            resource.add_filter(gtable.pe_id == table.pe_id)
            output = resource.exporter.json(resource,
                                            start=0,
                                            limit=limit,
                                            fields=[table.pe_id],
                                            orderby=field)
            items += json.loads(output)

        # Add Organisations
        if filter and value:
            otable = s3db.org_organisation
            field = otable.name
            query = field.lower().like("%" + value + "%")
            resource.clear_query()
            resource.add_filter(query)
            resource.add_filter(otable.pe_id == table.pe_id)
            output = resource.exporter.json(resource,
                                            start=0,
                                            limit=limit,
                                            fields=[table.pe_id],
                                            orderby=field)
            items += json.loads(output)

        items = [ { "id" : item[u'pe_id'],
                    "name" : s3db.pr_pentity_represent(item[u'pe_id'],
                                                       show_label=False) }
                  for item in items ]
        output = json.dumps(items)
        response.headers["Content-Type"] = "application/json"
        return output

# =============================================================================
class S3TrainingSearch(S3Search):
    """
        Search method with specifics for Trainign Event records
        - search coursed_id & site_id & return represents to the calling JS

        @ToDo: Allow searching by Date
    """

    def search_json(self, r, **attr):
        """
            JSON search method for S3TrainingAutocompleteWidget

            @param r: the S3Request
            @param attr: request attributes
        """

        response = current.response
        resource = self.resource
        table = self.table

        # Query comes in pre-filtered to accessible & deletion_status
        # Respect response.s3.filter
        resource.add_filter(response.s3.filter)

        _vars = self.request.vars # should be request.get_vars?

        # JQueryUI Autocomplete uses "term" instead of "value"
        # (old JQuery Autocomplete uses "q" instead of "value")
        value = _vars.value or _vars.term or _vars.q or None

        # We want to do case-insensitive searches
        # (default anyway on MySQL/SQLite, but not PostgreSQL)
        value = value.lower()

        filter = _vars.filter
        limit = int(_vars.limit or 0)

        if filter and value:

            s3db = current.s3db
            ctable = s3db.hrm_course
            field = ctable.name
            stable = s3db.org_site
            field2 = stable.name
            field3 = table.start_date

            # Fields to return
            fields = [table.id, field, field2, field3]

            if filter == "~":
                # hrm_training_event Autocomplete
                if " " in value:
                    value1, value2 = value.split(" ", 1)
                    value2 = value2.strip()
                    query = ((field.lower().like("%" + value1 + "%")) & \
                             (field2.lower().like(value2 + "%"))) | \
                            ((field.lower().like("%" + value2 + "%")) & \
                             (field2.lower().like(value1 + "%")))
                else:
                    value = value.strip()
                    query = ((field.lower().like("%" + value + "%")) | \
                             (field2.lower().like(value + "%")))

                #left = table.on(table.site_id == stable.id)
                query = query & (table.course_id == ctable.id) & \
                                (table.site_id == stable.id)

            else:
                output = current.manager.xml.json_message(
                                False,
                                400,
                                "Unsupported filter! Supported filters: ~"
                            )
                raise HTTP(400, body=output)

        resource.add_filter(query)

        if (not limit or limit > MAX_SEARCH_RESULTS) and resource.count() > MAX_SEARCH_RESULTS:
            output = jsons([dict(id="",
                                 name="Search results are over %d. Please input more characters." \
                                 % MAX_SEARCH_RESULTS)])

        else:
            attributes = dict(orderby=field)
            limitby = resource.limitby(start=0, limit=limit)
            if limitby is not None:
                attributes["limitby"] = limitby
            rows = resource.select(*fields, **attributes)
            output = []
            append = output.append
            for row in rows:
                record = dict(
                    id = row[table].id,
                    course = row[ctable].name,
                    site = row[stable].name,
                    date = S3DateTime.date_represent(row[table].start_date),
                    )
                append(record)
            output = jsons(output)


        response.headers["Content-Type"] = "application/json"
        return output


# =============================================================================
class S3SearchOrgHierarchyWidget(S3SearchOptionsWidget):
    def widget(self, resource, vars):
        field_name = self.field

        # check the field type
        try:
            field = resource.table[field_name]
        except:
            field_type = "virtual"
        else:
            field_type = str(field.type)

        return S3OrganisationHierarchyWidget()(field, {}, **self.attr)

# END =========================================================================
