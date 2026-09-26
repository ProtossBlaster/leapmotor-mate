"""Non-secret setup readiness; provisioning is owned by the distribution."""
from pathlib import Path
from leapmotor_cloud.private_storage import validate_private_file
import json
from leapmotor_cloud.account_password import AccountPasswordResolver
from session_material import certificate_usable


def readiness(cert_dir, *, parameters_directory=None):
    root = Path(cert_dir)
    cert, key = root / 'app.crt', root / 'app.key'
    present = cert.is_file() and key.is_file()
    ready = present and certificate_usable(cert, key)
    if ready:
        parameters = (Path(parameters_directory) if parameters_directory is not None
                      else root.parent / 'api-v2-private') / 'p12-parameters.json'
        try:
            validate_private_file(parameters)
            if parameters.stat().st_size > 32768:
                raise ValueError('Unsafe private parameters')
            AccountPasswordResolver(**json.loads(parameters.read_bytes()))
        except Exception:
            return {'present': False, 'managed': True,
                    'state': 'application_parameters_required', 'manual_upload_required': False}
    return {'present': bool(ready), 'managed': True,
            'state': 'ready' if ready else ('invalid_material' if present else 'provisioning_required'),
            'manual_upload_required': False}
