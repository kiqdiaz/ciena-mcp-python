class OTDRService:
    """Wrapper de /otdr/api/v1 (trazas OTDR: lanzar traza, listar y obtener el SOR parseado en JSON)."""

    def __init__(self, client):
        self.client = client

    def entities(self, session_id):
        """Entidades OTDR disponibles. Requiere una session_id de management activa sobre el NE."""
        return self.client.get("/otdr/api/v1/entities", params={"session_id": session_id})

    def start_stop_trace(self, session_id, entityid, trace_type="short", operation="start", direction="tx"):
        """Inicia o detiene una traza sobre una entidad OTDR (p.ej. entityid=OTDRCFG-1-5-8)."""
        body = {
            "parameters": {
                "trace_entities": [
                    {
                        "entityid": entityid,
                        "trace_type": trace_type,
                        "operation": operation,
                        "direction": direction,
                    }
                ]
            }
        }
        return self.client.post("/otdr/api/v1/entities", params={"session_id": session_id}, json=body)

    def tracelist(self, session_id, trace_id):
        """Lista de trazas SOR asociadas a una entidad OTDR."""
        return self.client.get("/otdr/api/v1/tracelist", params={"session_id": session_id, "trace_id": trace_id})

    def saved_traces(self, fre_id):
        """Trazas SOR ya guardadas en el MCP para un enlace (fre_id)."""
        return self.client.get("/otdr/api/v1/sortraces", params={"fre_id": fre_id})["data"]

    def retrieve_traces(self, fre_id):
        """Dispara la recuperación de trazas SOR para los extremos de fibra de un fre_id."""
        return self.client.post("/otdr/api/v1/sortraces", params={"fre_id": fre_id})

    def parse_traces(self, file_names):
        """Devuelve el contenido parseado (curva/eventos) de archivos .sor guardados, como JSON."""
        return self.client.post("/otdr/api/v1/sor/parse", json={"file_names": file_names})
