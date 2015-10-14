from __future__ import print_function

try:
    from urllib.parse import urlencode  # Python3
except ImportError:
    from urllib import urlencode  # Python2

from ripe.atlas.cousteau import AtlasRequest
from ripe.atlas.sagan import Result

from ..aggregators import RangeKeyAggregator, ValueKeyAggregator, aggregate
from ..exceptions import RipeAtlasToolsException
from ..helpers.validators import ArgumentType
from ..renderers import Renderer
from .base import Command as BaseCommand
from ..probes import Probe


class Command(BaseCommand):

    NAME = "report"

    DESCRIPTION = "Report the results of a measurement.\n\nExample:\n" \
                  "  ripe-atlas report 1001 --probes 157,10006\n"
    URLS = {
        "detail": "/api/v2/measurements/{}.json",
        "latest": "/api/v2/measurements/{}/latest.json",
        "results": "/api/v2/measurements/{}/results.json",
    }
    AGGREGATORS = {
        "country": ["probe.country_code", ValueKeyAggregator],
        "rtt-median": [
            "rtt_median",
            RangeKeyAggregator,
            [10, 20, 30, 40, 50, 100, 200, 300]
        ],
        "status": ["probe.status", ValueKeyAggregator],
        "asn_v4": ["probe.asn_v4", ValueKeyAggregator],
        "asn_v6": ["probe.asn_v6", ValueKeyAggregator],
        "prefix_v4": ["probe.prefix_v4", ValueKeyAggregator],
        "prefix_v6": ["probe.prefix_v6", ValueKeyAggregator],
    }

    def __init__(self, *args, **kwargs):
        BaseCommand.__init__(self, *args, **kwargs)
        self.payload = ""
        self.renderer = None

    def add_arguments(self):
        self.parser.add_argument(
            "measurement_id",
            type=int,
            help="The measurement id you want reported"
        )
        self.parser.add_argument(
            "--probes",
            type=ArgumentType.comma_separated_integers,
            help="A comma-separated list of probe ids you want to see "
                 "exclusively"
        )
        self.parser.add_argument(
            "--renderer",
            choices=Renderer.get_available(),
            help="The renderer you want to use. If this isn't defined, an "
                 "appropriate renderer will be selected."
        )
        self.parser.add_argument(
            "--aggregate-by",
            type=str,
            choices=self.AGGREGATORS.keys(),
            action="append",
            help="Tell the rendering engine to aggregate the results by the "
                 "selected option.  Note that if you opt for aggregation, no "
                 "output will be generated until all results are received."
        )
        self.parser.add_argument(
            "--start-time",
            type=ArgumentType.datetime,
            help="The start time of the report."
        )
        self.parser.add_argument(
            "--stop-time",
            type=ArgumentType.datetime,
            help="The stop time of the report."
        )

    def _get_latest_url(self):

        r = self.URLS["latest"]
        if self.arguments.start_time or self.arguments.stop_time:
            r = self.URLS["results"]

        r = r.format(self.arguments.measurement_id)

        query_arguments = {}
        if self.arguments.probes:
            query_arguments["probes"] = ",".join(self.arguments.probes)
        if self.arguments.start_time:
            query_arguments["start"] = self.arguments.start_time.timestamp
        if self.arguments.stop_time:
            query_arguments["stop"] = self.arguments.stop_time.timestamp

        if query_arguments:
            return "{}?{}".format(r, urlencode(query_arguments, doseq=True))

        return r

    def run(self):

        self.payload = ""
        pk = self.arguments.measurement_id
        measurement_exists, detail = AtlasRequest(
            url_path=self.URLS["detail"].format(pk)).get()

        if not measurement_exists:
            raise RipeAtlasToolsException("That measurement id does not exist")

        self.renderer = Renderer.get_renderer(
            self.arguments.renderer, detail["type"]["name"])()

        results = AtlasRequest(url_path=self._get_latest_url()).get()[1]

        if not results:
            raise RipeAtlasToolsException(
                "There aren't any results available for that measurement")

        description = detail["description"] or ""
        if description:
            description = "\n{}\n\n".format(description)

        self.payload += "\n" + self.renderer.on_start()

        sagans = self.create_enhanced_sagans(results)

        if self.arguments.aggregate_by:
            aggregators = self.get_aggregators()
            enhanced_results = aggregate(sagans, aggregators)
        else:
            enhanced_results = sagans

        self.multi_level_render(enhanced_results)

        self.payload += "\n" + self.renderer.on_finish()

        print(self.renderer.render(
            "reports/base.txt",
            measurement_id=self.arguments.measurement_id,
            description=description,
            payload=self.payload
        ), end="")

    def get_aggregators(self):
        """Return aggregators list based on user input"""
        aggregation_keys = []
        for aggr_key in self.arguments.aggregate_by:
            # Get class and aggregator key
            aggregation_class = self.AGGREGATORS[aggr_key][1]
            key = self.AGGREGATORS[aggr_key][0]
            if aggr_key == "rtt":
                # Get range for the aggregation
                key_range = self.AGGREGATORS[aggr_key][2]
                aggregation_keys.append(
                    aggregation_class(key=key, ranges=key_range)
                )
            else:
                aggregation_keys.append(aggregation_class(key=key))
        return aggregation_keys

    def create_enhanced_sagans(self, results):
        """
        Create Sagan Result objects and add additional an Probe attribute to
        each one of them.
        """

        sagans = []
        for result in results:
            sagans.append(
                Result.get(
                    result,
                    on_error=Result.ACTION_IGNORE,
                    on_malformation=Result.ACTION_IGNORE
                )
            )

        # Probes
        probes = self.arguments.probes
        if not probes:
            probes = set([r.probe_id for r in sagans])

        probes_dict = {}
        for probe in Probe.get_many(probes):
            probes_dict[probe.id] = probe

        # Attache a probe attribute to each sagan
        for sagan in sagans:
            sagan.probe = probes_dict[sagan.probe_id]

        return sagans

    def multi_level_render(self, aggregation_data, indent=""):
        """Traverses through aggregation data and print them indented"""

        if isinstance(aggregation_data, dict):

            for k, v in aggregation_data.items():
                self.payload = "{}{}{}\n".format(self.payload, indent, k)
                self.multi_level_render(v, indent=indent + " ")

        elif isinstance(aggregation_data, list):

            for index, data in enumerate(aggregation_data):
                res = self.renderer.on_result(data)
                if res:
                    self.payload = "{}{} {}".format(self.payload, indent, res)
