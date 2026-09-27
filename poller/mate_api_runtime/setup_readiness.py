"""Non-secret setup readiness; provisioning is owned by the distribution."""
from pathlib import Path
from leapmotor_cloud.private_storage import validate_private_file
import json
from leapmotor_cloud.account_password import AccountPasswordResolver
from session_material import certificate_usable


def _manual_upload_required():
    """Whether a supplied three-file bundle is the ONLY way to complete this installation.

    False wherever the certificate step can finish on its own: saving `app.crt`/`app.key` runs
    `provision_automatic`, which adds the common parameters from the profile packaged in the build.
    The setup page forks on this and not on `managed` — `managed` is true for every installation of
    the independent client, so forking on it offered a new user only the bundle box, and that bundle
    has to carry private parameters no user can produce (D #328).
    """
    try:
        from automatic_material import packaged_profile_usable
    except Exception:
        return True
    return not packaged_profile_usable()


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
                    'state': 'application_parameters_required',
                    'manual_upload_required': _manual_upload_required()}
    return {'present': bool(ready), 'managed': True,
            'state': 'ready' if ready else ('invalid_material' if present else 'provisioning_required'),
            'manual_upload_required': False if ready else _manual_upload_required()}
