from mcp_client import MCPClient, NSIService, PMService

if __name__ == "__main__":
    client = MCPClient()
    nsi = NSIService(client)
    pm = PMService(client)

    constructs = nsi.network_constructs()
    print(f"{len(constructs)} network constructs")

    ne0 = constructs[0]
    print("NE:", ne0["attributes"]["name"], ne0["attributes"]["displayData"]["displayName"])

    equipment = nsi.equipment(ne0["id"])
    trm_equipment = [e for e in equipment if "TRM" in e["attributes"]["displayData"]["displayName"]]
    print(f"{len(trm_equipment)} equipos TRM")

    metrics = pm.query_metrics(
        network_element_name=ne0["attributes"]["name"],
        facility_name_native="PTP-1-3-1",
        parameter="RX_OPTICAL_POWER",
    )
    for m in metrics:
        print(m)
