class NSIService:
    """Wrapper de /nsi/api/v7 (inventario de red: network constructs, equipment)."""

    def __init__(self, client):
        self.client = client

    def _get_all_pages(self, path, params=None):
        """La API pagina resultados (default limit=30, links.next para seguir)."""
        results = []
        resp = self.client.get(path, params=params)
        results.extend(resp["data"])
        next_url = resp.get("links", {}).get("next")
        while next_url:
            resp = self.client.get(next_url)
            results.extend(resp["data"])
            next_url = resp.get("links", {}).get("next")
        return results

    def network_constructs(self):
        return self._get_all_pages("/nsi/api/v7/networkConstructs")

    def equipment(self, network_construct_id):
        return self._get_all_pages(
            "/nsi/api/v7/equipment",
            params={"networkConstruct.id": network_construct_id},
        )

    def facility_resources(self, network_construct_id):
        """Enlaces (fres) tocados por un NE: userLabel, note y el otro extremo del enlace."""
        return self._get_all_pages(
            "/nsi/api/v7/fres",
            params={"networkConstruct.id": network_construct_id},
        )

    def tpes(self, network_construct_id):
        """Termination points de un NE (nativeName, p.ej. PTP-1-3-1, usable como --facility en --metrics)."""
        return self._get_all_pages(
            "/nsi/api/v7/tpes",
            params={"networkConstruct.id": network_construct_id},
        )
