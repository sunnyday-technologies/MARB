ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}

# Every value is supplied from a reviewed build record. No digest in this
# source tree claims that an image was built or approved.
ARG PYTHON_BASE_IMAGE
ARG REQUIREMENTS_LOCK_SHA256
ARG WHEELHOUSE_MANIFEST_SHA256
ARG CADCLAW_WHEEL_SHA256
ARG RUN_LIMITER_SHA256
ARG DOCKERFILE_SHA256
ARG CONTEXT_DOCKERIGNORE_SHA256
ARG BUILD_CONTEXT_MANIFEST_SHA256

LABEL org.sunnyday.marb.runtime-contract="marb-v0.12-h2b" \
      org.sunnyday.marb.cadclaw-version="0.10.0" \
      org.sunnyday.marb.cadclaw-commit="60fc271f68c8a794a4741f856b2dd4c9878416a6" \
      org.sunnyday.marb.cadclaw-pin-basis="marb_v0.12_frozen_functional_core" \
      org.sunnyday.marb.cadquery-version="2.7.0" \
      org.sunnyday.marb.cadquery-ocp-version="7.8.1.1.post1" \
      org.sunnyday.marb.base-image="${PYTHON_BASE_IMAGE}" \
      org.sunnyday.marb.requirements-lock-sha256="${REQUIREMENTS_LOCK_SHA256}" \
      org.sunnyday.marb.wheelhouse-manifest-sha256="${WHEELHOUSE_MANIFEST_SHA256}" \
      org.sunnyday.marb.cadclaw-wheel-sha256="${CADCLAW_WHEEL_SHA256}" \
      org.sunnyday.marb.run-limiter-sha256="${RUN_LIMITER_SHA256}" \
      org.sunnyday.marb.dockerfile-sha256="${DOCKERFILE_SHA256}" \
      org.sunnyday.marb.context-dockerignore-sha256="${CONTEXT_DOCKERIGNORE_SHA256}" \
      org.sunnyday.marb.build-context-manifest-sha256="${BUILD_CONTEXT_MANIFEST_SHA256}"

WORKDIR /opt/marb
COPY requirements.lock /opt/marb/runtime-requirements.lock
COPY run_limited.py /opt/marb/run_limited.py
COPY wheelhouse.sha256 /opt/marb/runtime-wheelhouse.sha256
COPY build-context.sha256 /opt/marb/build-context.sha256
COPY wheelhouse/ /opt/marb/wheelhouse/

RUN /usr/local/bin/python3 -c "import os,re; values=('REQUIREMENTS_LOCK_SHA256','WHEELHOUSE_MANIFEST_SHA256','CADCLAW_WHEEL_SHA256','RUN_LIMITER_SHA256','DOCKERFILE_SHA256','CONTEXT_DOCKERIGNORE_SHA256','BUILD_CONTEXT_MANIFEST_SHA256'); assert all(re.fullmatch(r'[0-9a-f]{64}', os.environ[name]) for name in values); base=os.environ['PYTHON_BASE_IMAGE']; assert re.fullmatch(r'[a-z0-9][a-z0-9._:/-]{1,254}@sha256:[0-9a-f]{64}', base) and '//' not in base" \
    && printf '%s  %s\n' "$REQUIREMENTS_LOCK_SHA256" /opt/marb/runtime-requirements.lock | sha256sum -c - \
    && printf '%s  %s\n' "$RUN_LIMITER_SHA256" /opt/marb/run_limited.py | sha256sum -c - \
    && printf '%s  %s\n' "$WHEELHOUSE_MANIFEST_SHA256" /opt/marb/runtime-wheelhouse.sha256 | sha256sum -c - \
    && printf '%s  %s\n' "$BUILD_CONTEXT_MANIFEST_SHA256" /opt/marb/build-context.sha256 | sha256sum -c - \
    && cd /opt/marb \
    && /usr/local/bin/python3 -c "import os,re; from pathlib import Path; root=Path('/opt/marb/wheelhouse'); files=list(root.iterdir()); assert files and all(p.is_file() and not p.is_symlink() and p.suffix == '.whl' for p in files); lines=Path('/opt/marb/runtime-wheelhouse.sha256').read_text(encoding='ascii').splitlines(); matches=[re.fullmatch(r'([0-9a-f]{64})  (wheelhouse/[A-Za-z0-9][A-Za-z0-9._+-]*\.whl)', line) for line in lines]; assert lines and all(matches); wheel_entries={item.group(2):item.group(1) for item in matches}; wheel_paths=['wheelhouse/'+p.name for p in files]; assert len(wheel_entries) == len(lines) and sorted(wheel_entries) == sorted(wheel_paths); context_lines=Path('/opt/marb/build-context.sha256').read_text(encoding='ascii').splitlines(); context_matches=[re.fullmatch(r'([0-9a-f]{64})  (Dockerfile|\.dockerignore|requirements\.lock|run_limited\.py|wheelhouse\.sha256|wheelhouse/[A-Za-z0-9][A-Za-z0-9._+-]*\.whl)', line) for line in context_lines]; assert context_lines and all(context_matches); context_entries={item.group(2):item.group(1) for item in context_matches}; assert len(context_entries) == len(context_lines) and [item.group(2) for item in context_matches] == sorted(context_entries); assert set(context_entries) == {'Dockerfile','.dockerignore','requirements.lock','run_limited.py','wheelhouse.sha256',*wheel_entries}; assert context_entries['Dockerfile'] == os.environ['DOCKERFILE_SHA256']; assert context_entries['.dockerignore'] == os.environ['CONTEXT_DOCKERIGNORE_SHA256']; assert context_entries['requirements.lock'] == os.environ['REQUIREMENTS_LOCK_SHA256']; assert context_entries['run_limited.py'] == os.environ['RUN_LIMITER_SHA256']; assert context_entries['wheelhouse.sha256'] == os.environ['WHEELHOUSE_MANIFEST_SHA256']; assert all(context_entries[path] == digest for path,digest in wheel_entries.items()); assert context_entries['wheelhouse/cadclaw-0.10.0-py3-none-any.whl'] == os.environ['CADCLAW_WHEEL_SHA256']; cad=[p.name for p in files if p.name.split('-',1)[0].replace('_','-').lower() == 'cadclaw']; assert cad == ['cadclaw-0.10.0-py3-none-any.whl']" \
    && sha256sum -c runtime-wheelhouse.sha256 \
    && printf '%s  %s\n' "$CADCLAW_WHEEL_SHA256" /opt/marb/wheelhouse/cadclaw-0.10.0-py3-none-any.whl | sha256sum -c - \
    && /usr/local/bin/python3 -m pip install --disable-pip-version-check --no-cache-dir --no-index --no-deps --only-binary=:all: /opt/marb/wheelhouse/cadclaw-0.10.0-py3-none-any.whl \
    && /usr/local/bin/python3 -m pip install --disable-pip-version-check --no-cache-dir --no-index --only-binary=:all: --find-links=/opt/marb/wheelhouse --requirement /opt/marb/runtime-requirements.lock \
    && printf '%s\n' "{\"cadclaw_commit\":\"60fc271f68c8a794a4741f856b2dd4c9878416a6\",\"cadclaw_pin_basis\":\"marb_v0.12_frozen_functional_core\",\"cadclaw_version\":\"0.10.0\",\"cadquery_ocp_version\":\"7.8.1.1.post1\",\"cadquery_version\":\"2.7.0\",\"run_limiter_sha256\":\"$RUN_LIMITER_SHA256\"}" > /opt/marb/runtime.json \
    && /usr/local/bin/python3 -c "import base64,csv,hashlib,importlib.metadata as m,json,os,sys; from pathlib import Path; expected={'cadclaw_version':'0.10.0','cadclaw_commit':'60fc271f68c8a794a4741f856b2dd4c9878416a6','cadclaw_pin_basis':'marb_v0.12_frozen_functional_core','cadquery_version':'2.7.0','cadquery_ocp_version':'7.8.1.1.post1','run_limiter_sha256':os.environ['RUN_LIMITER_SHA256']}; assert sys.version_info[:2] == (3, 11); assert json.loads(Path('/opt/marb/runtime.json').read_text()) == expected; assert {'cadclaw_version':m.version('cadclaw'),'cadquery_version':m.version('cadquery'),'cadquery_ocp_version':m.version('cadquery-ocp')} == {k:v for k,v in expected.items() if k not in {'cadclaw_commit','cadclaw_pin_basis','run_limiter_sha256'}}; assert expected['run_limiter_sha256'] == hashlib.sha256(Path('/opt/marb/run_limited.py').read_bytes()).hexdigest(); dist=m.distribution('cadclaw'); direct=json.loads(dist.read_text('direct_url.json')); archive=direct['archive_info']; observed=archive.get('hashes',{}).get('sha256') or archive.get('hash','').removeprefix('sha256='); assert observed == os.environ['CADCLAW_WHEEL_SHA256']; rows=list(csv.reader(dist.read_text('RECORD').splitlines())); assert rows and all((not spec and (rel.endswith('.pyc') or rel.endswith('/RECORD'))) or (spec.startswith('sha256=') and hashlib.sha256(Path(dist.locate_file(rel)).read_bytes()).digest() == base64.urlsafe_b64decode(spec.split('=',1)[1]+'===')) for rel,spec,size in rows); import cadclaw,cadquery,OCP" \
    && /usr/local/bin/python3 -c "import json,os; from pathlib import Path; keys={'base_image':'PYTHON_BASE_IMAGE','requirements_lock_sha256':'REQUIREMENTS_LOCK_SHA256','wheelhouse_manifest_sha256':'WHEELHOUSE_MANIFEST_SHA256','cadclaw_wheel_sha256':'CADCLAW_WHEEL_SHA256','run_limiter_sha256':'RUN_LIMITER_SHA256','dockerfile_sha256':'DOCKERFILE_SHA256','context_dockerignore_sha256':'CONTEXT_DOCKERIGNORE_SHA256','build_context_manifest_sha256':'BUILD_CONTEXT_MANIFEST_SHA256'}; value={'schema':'marb_h2b_image_build_provenance.v2',**{key:os.environ[name] for key,name in keys.items()}}; Path('/opt/marb/build-provenance.json').write_text(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n',encoding='ascii')" \
    && chmod 0555 /opt/marb/run_limited.py \
    && chmod 0444 /opt/marb/runtime.json /opt/marb/build-provenance.json /opt/marb/runtime-requirements.lock /opt/marb/runtime-wheelhouse.sha256 /opt/marb/build-context.sha256 \
    && rm -rf /opt/marb/wheelhouse /root/.cache

RUN mkdir -p /workspace /tmp/cache \
    && chown -R 65532:65532 /workspace /tmp/cache \
    && chmod 0700 /workspace /tmp/cache

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    TZ=UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /workspace
USER 65532:65532
ENTRYPOINT []
CMD []
