import io
import json
from pathlib import Path
import zipfile
import pytest
import mate_api


def bundle(entries):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:
        for name,value in entries:z.writestr(name,value)
    return output.getvalue()


def test_bundle_rejects_extra_account_data_before_writing(tmp_path):
    from application_bundle import install_bundle
    with pytest.raises(ValueError):
        install_bundle(bundle([('secret.key',b'account data')]),tmp_path)
    assert not list(tmp_path.iterdir())


def test_bundle_rejects_traversal_and_duplicate_members(tmp_path):
    from application_bundle import install_bundle
    for entries in [[('../certs/app.crt',b'x')],[('certs/app.crt',b'x'),('certs/app.crt',b'y')]]:
        with pytest.raises(ValueError):install_bundle(bundle(entries),tmp_path)
    assert not list(tmp_path.iterdir())


def test_bundle_only_installs_validated_application_files(tmp_path,monkeypatch):
    import application_bundle as mod
    names=mod.NAMES
    payload=bundle([(name,b'synthetic') for name in names])
    captured={}
    def bootstrap(source,destination):
        captured['names']=sorted(str(p.relative_to(source)) for p in Path(source).rglob('*') if p.is_file())
        captured['destination']=destination
        return {'state':'application_material_migrated'}
    monkeypatch.setattr(mod,'bootstrap',bootstrap)
    assert mod.install_bundle(payload,tmp_path)['state']=='application_material_migrated'
    assert captured['names']==sorted(names)
    assert captured['destination']==tmp_path
