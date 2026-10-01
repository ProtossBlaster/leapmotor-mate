"""The cloud client both processes use: Mate's own (MATE-API, V3 commands).

Since 01/10/2026 there is no other. The bundled third-party SDK, the `MATE_API_V2` switch that
selected it and the qualification that chose between the two are gone, so a `legacy` decision
stored by an older version changes nothing.
"""
from api_v2_bridge import NewAPIClient as LeapmotorApiClient
