class PMService:
    """Wrapper de /pm/api/v3 (métricas de performance monitoring)."""

    def __init__(self, client):
        self.client = client

    def query_metrics(self, network_element_name, facility_name_native, parameter,
                       range_unit="HOURS", range_value=1):
        body = {
            "data": {
                "type": "queryMetrics",
                "attributes": {
                    "range": {
                        "type": "relative",
                        "unit": range_unit,
                        "value": range_value,
                    },
                    "filter": [
                        "and",
                        ["=", "networkElementName", network_element_name],
                        ["=", "facilityNameNative", facility_name_native],
                        ["=", "parameter", parameter],
                    ],
                },
            }
        }
        return self.client.post(
            "/pm/api/v3/query/metrics",
            params={"ts": "rfc3339"},
            json=body,
        )["data"]
