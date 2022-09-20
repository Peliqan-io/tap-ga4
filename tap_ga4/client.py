from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (DateRange, Dimension,
                                                Metric, OrderBy,
                                                RunReportRequest,
                                                GetMetadataRequest,
                                                CheckCompatibilityRequest)
from google.oauth2.credentials import Credentials
from google.api_core.exceptions import (ResourceExhausted, ServerError,
                                        TooManyRequests)
import singer
import backoff



class Client:

    PAGE_SIZE = 100000
    LOGGER = singer.get_logger()

    def __init__(self, config):
        credentials = Credentials(None,
                              refresh_token=config["refresh_token"],
                              token_uri='https://www.googleapis.com/oauth2/v4/token',
                              client_id=config["oauth_client_id"],
                              client_secret=config["oauth_client_secret"])

        self.client = BetaAnalyticsDataClient(credentials=credentials)


    def seconds_to_next_hour():
        current_utc_time = utils.now()
        # Get a time 10 seconds past the hour to be sure we don't make another
        # request before Google resets quota.
        next_hour = (current_utc_time + timedelta(hours=1)).replace(minute=0, second=10, microsecond=0)
        time_till_next_hour = (next_hour - current_utc_time).seconds
        return time_till_next_hour


    def sleep_if_quota_reached(ex):
        if isinstance(ex, ResourceExhausted):
            seconds = seconds_to_next_hour()
            LOGGER.info("Reached hourly quota limit. Sleeping %s seconds.", seconds)
            time.sleep(seconds)
        return False


    @backoff.on_exception(backoff.expo,
                          (ServerError, TooManyRequests, ResourceExhausted),
                          max_tries=5,
                          jitter=None,
                          giveup=sleep_if_quota_reached,
                          logger=None)
    def _make_request(self, request):
        if type(request) == RunReportRequest:
            return self.client.run_report(request)
        if type(request) == GetMetadataRequest:
            return self.client.get_metadata(request)
        if type(request) == CheckCompatibilityRequest:
            return self.client.check_compatibility(request)

    def get_report(self, report, range_start_date, range_end_date):
        """
        Calls run_report and paginates over the request if the
        response.row_count is greater than 100,000.
        """
        offset = 0
        has_more_rows = True
        while has_more_rows:
            request = RunReportRequest(
                property=f"properties/{report['property_id']}",
                dimensions=report["dimensions"],
                metrics=report["metrics"],
                date_ranges=[DateRange(start_date=range_start_date, end_date=range_end_date)],
                limit=self.PAGE_SIZE,
                offset=offset,
                return_property_quota=True,
                order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date", order_type="NUMERIC"))]
            )

            response = self._make_request(request)
            has_more_rows = response.row_count > self.PAGE_SIZE + offset
            offset += self.PAGE_SIZE

            self.LOGGER.info("Request for report: %s from %s -> %s consumed %s GA4 quota tokens",
                        report["name"],
                        range_start_date,
                        range_end_date,
                        response.property_quota.tokens_per_hour.consumed)

            yield response


    def get_dimensions_and_metrics(self, property_id):
        request = GetMetadataRequest(
            name=f"properties/{property_id}/metadata",
        )
        return self._make_request(request)


    def check_metric_compatibility(self, property_id, metric):
        request = CheckCompatibilityRequest(
            property=f"properties/{property_id}",
            metrics=[Metric(name=metric.api_name)],
            compatibility_filter="INCOMPATIBLE"
            )
        return self._make_request(request)

    def check_dimension_compatibility(self, property_id, dimension):
        request = CheckCompatibilityRequest(
            property=f"properties/{property_id}",
            dimensions=[Dimension(name=dimension.api_name)],
            compatibility_filter="INCOMPATIBLE"
            )
        return self._make_request(request)
