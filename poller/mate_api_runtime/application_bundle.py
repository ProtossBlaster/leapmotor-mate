"""Install exactly three application files; never accept account/session data."""
import io
import os
from pathlib import Path
from leapmotor_cloud.private_storage import ensure_private_directory
import tempfile
import zipfile
from bootstrap_independent import bootstrap, NAMES

MAX_BYTES = 128 * 1024

def install_bundle(payload, destination):
    if not isinstance(payload, bytes) or len(payload) > MAX_BYTES:
        raise ValueError('Invalid application bundle size')
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members=archive.infolist()
            if len(members)!=len(NAMES) or {m.filename for m in members}!=set(NAMES):
                raise ValueError('Application bundle must contain exactly the required files')
            if any(m.file_size > 32768 or m.flag_bits & 1 for m in members):
                raise ValueError('Invalid application bundle entry')
            contents={m.filename:archive.read(m) for m in members}
    except (zipfile.BadZipFile, RuntimeError) as error:
        raise ValueError('Invalid application bundle') from None
    # Only fixed, validated paths are written; never extract archive paths.
    with tempfile.TemporaryDirectory(prefix='mate-application-') as directory:
        root=ensure_private_directory(Path(directory))
        for name,content in contents.items():
            target=root/name
            ensure_private_directory(target.parent)
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(content)
        return bootstrap(source=root,destination=destination)
