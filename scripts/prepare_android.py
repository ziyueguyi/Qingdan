"""Copy shared source/assets and Dulwich's supported pure-Python fallback for Android."""
from pathlib import Path
import importlib.metadata
import shutil
import dulwich

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'android/app/build/generated'
python=DEST/'python';assets=DEST/'assets'
python.mkdir(parents=True,exist_ok=True);assets.mkdir(parents=True,exist_ok=True)
shutil.copytree(ROOT/'src/qingdan',python/'qingdan',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','web','shell','api'))
shutil.copytree(ROOT/'src/qingdan/web',assets/'web',dirs_exist_ok=True)
shutil.copytree(Path(dulwich.__file__).parent,python/'dulwich',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyd','*.so','tests'))
dist=importlib.metadata.distribution('dulwich')
shutil.copytree(Path(dist._path),python/Path(dist._path).name,dirs_exist_ok=True)
index=assets/'web/index.html'
html=index.read_text(encoding='utf-8').replace('<html','<html data-platform="android"',1)
html=html.replace('<body>','<body><button class="phone-menu-btn" data-act="mobile-nav" aria-label="Menu">☰</button>')
html=html.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\'; style-src \'self\' \'unsafe-inline\'; img-src \'self\' data:; connect-src \'self\'; frame-src \'none\'; object-src \'none\'">')
index.write_text(html,encoding='utf-8')
print('Prepared shared Android sources and offline web assets')
