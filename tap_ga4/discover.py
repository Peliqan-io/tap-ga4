from collections import defaultdict
from functools import reduce
import singer
from singer import Catalog, CatalogEntry, Schema, metadata
from singer.catalog import write_catalog
import json

LOGGER = singer.get_logger()


DIMENSION_INTEGER_FIELD_OVERRIDES = {"cohortNthDay",
                                     "cohortNthMonth",
                                     "cohortNthWeek",
                                     "day",
                                     "dayOfWeek",
                                     "hour",
                                     "minute",
                                     "month",
                                     "nthDay",
                                     "nthHour",
                                     "nthMinute",
                                     "nthMonth",
                                     "nthWeek",
                                     "nthYear",
                                     "percentScrolled",
                                     "week",
                                     "year"}

DIMENSION_DATETIME_FIELD_OVERRIDES = {"date",
                                      "dateHour",
                                      "dateHourMinute",
                                      "firstSessionDate"}

FLOAT_TYPES = {"TYPE_FLOAT",
               "TYPE_SECONDS",
               "TYPE_MILLISECONDS",
               "TYPE_MINUTES",
               "TYPE_HOURS",
               "TYPE_STANDARD",
               "TYPE_CURRENCY",
               "TYPE_FEET",
               "TYPE_MILES",
               "TYPE_METERS",
               "TYPE_KILOMETERS"}

# Cohort is incompatible with `date`, which is required.
INCOMPATIBLE_CATEGORIES = {"Cohort"}


def add_metrics_to_schema(schema, metrics):
    for metric in metrics:
        metric_type = metric.type_.name
        if metric_type == "TYPE_INTEGER":
            schema["properties"][metric.api_name] = {"type": ["integer", "null"]}
        elif metric_type in FLOAT_TYPES:
            schema["properties"][metric.api_name] = {"type": ["number", "null"]}
        else:
            raise Exception(f"Unknown Google Analytics 4 type: {metric_type}")


def add_dimensions_to_schema(schema, dimensions):
    for dimension in dimensions:
        if dimension.api_name in DIMENSION_INTEGER_FIELD_OVERRIDES:
            schema["properties"][dimension.api_name] = {"type": ["integer", "null"]}
        elif dimension.api_name in DIMENSION_DATETIME_FIELD_OVERRIDES:
            # datetime is not always a valid datetime string
            # https://support.google.com/analytics/answer/9309767
            schema["properties"][dimension.api_name] = \
                {"anyOf": [
                    {"type": ["string", "null"], "format": "date-time"},
                    {"type": ["string", "null"]}
                ]}
        else:
            schema["properties"][dimension.api_name] = {"type": ["string", "null"]}


def generate_base_schema():
    return {"type": "object", "properties": {"_sdc_record_hash": {"type": "string"},
                                             "property_id": {"type": "string"},
                                             "account_id": {"type": "string"}}}



def generate_metadata(schema, dimensions, metrics, field_exclusions):
    mdata = metadata.get_standard_metadata(schema=schema, key_properties=["_sdc_record_hash"], valid_replication_keys=["date"],
                                           replication_method=["INCREMENTAL"])
    mdata = metadata.to_map(mdata)
    mdata = reduce(lambda mdata, field_name: metadata.write(mdata, ("properties", field_name), "inclusion", "automatic"),
                   ["_sdc_record_hash", "property_id", "account_id", "date"],
                   mdata)
    mdata = reduce(lambda mdata, field_name: metadata.write(mdata, ("properties", field_name), "tap_ga4.group", "Report Field"),
                   ["_sdc_record_hash", "property_id", "account_id"],
                   mdata)
    for dimension in dimensions:
        mdata = metadata.write(mdata, ("properties", dimension.api_name), "tap_ga4.group", dimension.category)
        mdata = metadata.write(mdata, ("properties", dimension.api_name), "behavior", "DIMENSION")
        mdata = metadata.write(mdata, ("properties", dimension.api_name), "fieldExclusions", field_exclusions[dimension.api_name])
    for metric in metrics:
        mdata = metadata.write(mdata, ("properties", metric.api_name), "tap_ga4.group", metric.category)
        mdata = metadata.write(mdata, ("properties", metric.api_name), "behavior", "METRIC")
        mdata = metadata.write(mdata, ("properties", metric.api_name), "fieldExclusions", field_exclusions[metric.api_name])
    return mdata


def generate_schema_and_metadata(dimensions, metrics, field_exclusions):
    LOGGER.info("Discovering fields")
    schema = generate_base_schema()
    add_dimensions_to_schema(schema, dimensions)
    add_metrics_to_schema(schema, metrics)
    mdata = generate_metadata(schema, dimensions, metrics, field_exclusions)
    return schema, mdata


def generate_catalog(reports, dimensions, metrics, field_exclusions):
    schema, mdata = generate_schema_and_metadata(dimensions, metrics, field_exclusions)
    catalog_entries = []
    LOGGER.info("Generating catalog")
    for report in reports:
        catalog_entries.append(CatalogEntry(schema=Schema.from_dict(schema),
                                            key_properties=["_sdc_record_hash"],
                                            stream=report["name"],
                                            tap_stream_id=report["id"],
                                            metadata=metadata.to_list(mdata)))

    return Catalog(catalog_entries)


def get_field_exclusions(client, property_id, dimensions, metrics):
    field_exclusions = defaultdict(list)
    with open("field_exclusions.json", "r") as infile:
        field_exclusions.update(json.load(infile))

    LOGGER.info("Discovering dimension field exclusions")
    for dimension in dimensions:
        if dimension.api_name in field_exclusions:
            continue
        res = client.check_dimension_compatibility(property_id, dimension)
        for field in res.dimension_compatibilities:
            field_exclusions[dimension.api_name].append(
                field.dimension_metadata.api_name)
        for field in res.metric_compatibilities:
            field_exclusions[dimension.api_name].append(
                field.metric_metadata.api_name)

    LOGGER.info("Discovering metric field exclusions")
    for metric in metrics:
        if metric.api_name in field_exclusions:
            continue
        res = client.check_metric_compatibility(property_id, metric)
        for field in res.dimension_compatibilities:
            field_exclusions[metric.api_name].append(field.dimension_metadata.api_name)
        for field in res.metric_compatibilities:
            field_exclusions[metric.api_name].append(field.metric_metadata.api_name)

    return field_exclusions


def get_dimensions_and_metrics(client, property_id):
    response = client.get_dimensions_and_metrics(property_id)
    dimensions = [dimension for dimension in response.dimensions
                  if dimension.category not in INCOMPATIBLE_CATEGORIES]
    metrics = [metric for metric in response.metrics
               if metric.category not in INCOMPATIBLE_CATEGORIES]
    return dimensions, metrics


def get_default_dimensions_and_metrics(client, property_id):
    d, m = get_dimensions_and_metrics(client, 0)

    fields = defaultdict(list)
    for dimension in d:
        res = client.check_dimension_compatibility(property_id, dimension)
        for field in res.dimension_compatibilities:
            fields[dimension.api_name].append(field.dimension_metadata.api_name)
        for field in res.metric_compatibilities:
            fields[dimension.api_name].append(field.metric_metadata.api_name)

    for metric in m:
        res = client.check_metric_compatibility(property_id, metric)
        for field in res.dimension_compatibilities:
            fields[metric.api_name].append(field.dimension_metadata.api_name)
        for field in res.metric_compatibilities:
            fields[metric.api_name].append(field.metric_metadata.api_name)

    with open("/opt/code/tap-ga4/tap_ga4/field_exclusions.json", "w") as outfile:
        fields_json = json.dumps(fields)
        outfile.write(fields_json)


def discover(client, reports, property_id):
    # get_default_dimensions_and_metrics(client, property_id)
    dimensions, metrics = get_dimensions_and_metrics(client, property_id)
    field_exclusions = get_field_exclusions(client, property_id, dimensions, metrics)
    catalog = generate_catalog(reports, dimensions, metrics, field_exclusions)
    write_catalog(catalog)
