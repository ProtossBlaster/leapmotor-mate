"""A release candidate must never advance latest or the stable HA add-on."""
import ast
import os
from pathlib import Path
import re


def prerelease(version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|alpha|beta)\.[0-9]+)?", version):
        raise ValueError('Unsupported release version')
    return '-' in version

if __name__ == '__main__':
    tree=ast.parse(Path('web/main.py').read_text())
    version=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id=='MATE_VERSION' for t in n.targets))
    value='true' if prerelease(version) else 'false'
    with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('prerelease='+value+'\n')
