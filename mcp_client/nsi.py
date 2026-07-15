class NSIService:
    """Wrapper de /nsi/api/v7 (inventario de red: network constructs, equipment)."""

    def __init__(self, client):
        self.client = client

    def network_constructs(self):
        return self.client.get("/nsi/api/v7/networkConstructs")["data"]

    def equipment(self, network_construct_id):
        return self.client.get(
            "/nsi/api/v7/equipment",
            params={"networkConstruct.id": network_construct_id},
        )["data"]

    def facility_resources(self, network_construct_id):
        """Enlaces (fres) tocados por un NE: userLabel, note y el otro extremo del enlace."""
        return self.client.get(
            "/nsi/api/v7/fres",
            params={"networkConstruct.id": network_construct_id},
        )["data"]
