class NSIService:
    """Wrapper de /nsi/api/v7 (inventario de red: network constructs, equipment)."""

    def __init__(self, client):
        self.client = client

    def _get_all_pages(self, path, params=None):
        """La API pagina resultados (default limit=30, links.next para seguir).

        El cursor de paginación a veces solapa el último item de una página con
        el primero de la siguiente, duplicándolo; se deduplica por id preservando
        el orden de llegada.
        """
        seen_ids = set()
        results = []

        def add_page(items):
            for item in items:
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    results.append(item)

        resp = self.client.get(path, params=params)
        add_page(resp["data"])
        next_url = resp.get("links", {}).get("next")
        while next_url:
            resp = self.client.get(next_url)
            add_page(resp["data"])
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
