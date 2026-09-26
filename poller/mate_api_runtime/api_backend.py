"""Choose one API backend at process startup; never retry through another backend.

mate_api configures activation before importing clients. Only an explicit legacy
startup decision selects the bundled SDK; new and unspecified installs keep the
independent client's rights and qualification checks.
"""
import os

if os.environ.get("MATE_API_V2") == "0":
    from leapmotor_api import LeapmotorApiClient
else:
    from api_v2_bridge import NewAPIClient as LeapmotorApiClient
