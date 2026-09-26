"""Explicit retirement only, while all users are quiescent. Never auto-scan/delete."""
import stat
from pathlib import Path
from .errors import ValidationError
from .private_storage import validate_private_directory


def retire_generations(root, retired, *, active_paths, quiescent=False):
    if quiescent is not True or not isinstance(root,Path):
        raise ValidationError('Quiescent readers and explicit private root required')
    try:
        validate_private_directory(root)
    except (ValueError, OSError):
        raise ValidationError('Private root required') from None
    root=root.resolve()
    active={Path(p).resolve().parent for p in active_paths}
    selected=[]
    for item in retired:
        item=Path(item)
        if item.is_symlink() or item.parent.resolve()!=root or not item.name.startswith('generation-'):
            raise ValidationError('Invalid retired generation')
        if item.resolve() in active:raise ValidationError('Generation still referenced')
        if not item.exists():continue
        if not item.is_dir():raise ValidationError('Invalid generation directory')
        children=list(item.iterdir())
        if {p.name for p in children}-{'cert.pem','key.pem'}:
            raise ValidationError('Unexpected files in generation')
        if any(not stat.S_ISREG(p.lstat().st_mode) or p.lstat().st_nlink!=1 for p in children):
            raise ValidationError('Unsafe generation contents')
        if item not in [p for p,_ in selected]:selected.append((item,children))
    # Validate the entire explicit manifest before any deletion.
    for item,children in selected:
        for child in children:child.unlink()
        item.rmdir()
    return len(selected)
