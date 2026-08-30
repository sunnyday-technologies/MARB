ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}

# Every value is supplied from a reviewed build record. No digest in this
# source tree claims that an image was built or approved.
ARG PYTHON_BASE_IMAGE
ARG REQUIREMENTS_LOCK_SHA256
ARG WHEELHOUSE_MANIFEST_SHA256
ARG CADCLAW_WHEEL_SHA256
ARG RUN_LIMITER_SHA256
ARG NATIVE_DEB_LOCK_SHA256
ARG NATIVE_DEB_MANIFEST_SHA256
ARG NATIVE_BUNDLE_VERIFIER_SHA256
ARG DOCKERFILE_SHA256
ARG CONTEXT_DOCKERIGNORE_SHA256
ARG BUILD_CONTEXT_MANIFEST_SHA256

LABEL org.sunnyday.marb.runtime-contract="marb-v0.13-h2b" \
      org.sunnyday.marb.runtime-contract-sha256="2b5c4d9fef3189d95a6d8e550bb50f2cf7a7d0171000bd130c7bd18ecf9a7d80" \
      org.sunnyday.marb.cadclaw-version="0.10.0" \
      org.sunnyday.marb.cadclaw-commit="fad0dd552a49a0b32336f1845c2b82873ad6360a" \
      org.sunnyday.marb.cadclaw-gate-spec-version="0.13.0" \
      org.sunnyday.marb.cadclaw-gate-registry-version="harness-gates.v1" \
      org.sunnyday.marb.cadclaw-pin-basis="marb_v0.13_calibrated_cadclaw_fad0dd55" \
      org.sunnyday.marb.cadclaw-source-manifest-sha256="6b6cfd465cd6b32cc90f7a8631601e63830f130553a2c593b45ab2d8302b6831" \
      org.sunnyday.marb.cadclaw-calibration-sha256="64956f829563978bcfc229b43c773ab2d67828ca79808df71effa3e37b8c4e84" \
      org.sunnyday.marb.cadquery-version="2.7.0" \
      org.sunnyday.marb.cadquery-ocp-version="7.8.1.1.post1" \
      org.sunnyday.marb.base-image="${PYTHON_BASE_IMAGE}" \
      org.sunnyday.marb.requirements-lock-sha256="${REQUIREMENTS_LOCK_SHA256}" \
      org.sunnyday.marb.wheelhouse-manifest-sha256="${WHEELHOUSE_MANIFEST_SHA256}" \
      org.sunnyday.marb.cadclaw-wheel-sha256="${CADCLAW_WHEEL_SHA256}" \
      org.sunnyday.marb.run-limiter-sha256="${RUN_LIMITER_SHA256}" \
      org.sunnyday.marb.native-deb-lock-sha256="${NATIVE_DEB_LOCK_SHA256}" \
      org.sunnyday.marb.native-deb-manifest-sha256="${NATIVE_DEB_MANIFEST_SHA256}" \
      org.sunnyday.marb.native-bundle-verifier-sha256="${NATIVE_BUNDLE_VERIFIER_SHA256}" \
      org.sunnyday.marb.native-deb-package-count="39" \
      org.sunnyday.marb.native-deb-total-bytes="48570480" \
      org.sunnyday.marb.dockerfile-sha256="${DOCKERFILE_SHA256}" \
      org.sunnyday.marb.context-dockerignore-sha256="${CONTEXT_DOCKERIGNORE_SHA256}" \
      org.sunnyday.marb.build-context-manifest-sha256="${BUILD_CONTEXT_MANIFEST_SHA256}"

WORKDIR /opt/marb
COPY requirements.lock /opt/marb/runtime-requirements.lock
COPY run_limited.py /opt/marb/run_limited.py
COPY runtime-contract.v0.13.json /opt/marb/runtime-contract.json
COPY cadclaw-calibration.fad0dd55.json /opt/marb/cadclaw-calibration.json
COPY wheelhouse.sha256 /opt/marb/runtime-wheelhouse.sha256
COPY native-debs.lock.json /opt/marb/native-debs.lock.json
COPY verify_native_bundle.py /opt/marb/verify_native_bundle.py
COPY native-debs.sha256 /opt/marb/native-debs.sha256
COPY build-context.sha256 /opt/marb/build-context.sha256
COPY wheelhouse/ /opt/marb/wheelhouse/
COPY native-debs/ /opt/marb/native-debs/

RUN /usr/local/bin/python3 -c "import os,re; values=('REQUIREMENTS_LOCK_SHA256','WHEELHOUSE_MANIFEST_SHA256','CADCLAW_WHEEL_SHA256','RUN_LIMITER_SHA256','NATIVE_DEB_LOCK_SHA256','NATIVE_DEB_MANIFEST_SHA256','NATIVE_BUNDLE_VERIFIER_SHA256','DOCKERFILE_SHA256','CONTEXT_DOCKERIGNORE_SHA256','BUILD_CONTEXT_MANIFEST_SHA256'); assert all(re.fullmatch(r'[0-9a-f]{64}', os.environ[name]) for name in values); base=os.environ['PYTHON_BASE_IMAGE']; assert re.fullmatch(r'[a-z0-9][a-z0-9._:/-]{1,254}@sha256:[0-9a-f]{64}', base) and '//' not in base" \
    && printf '%s  %s\n' "$REQUIREMENTS_LOCK_SHA256" /opt/marb/runtime-requirements.lock | sha256sum -c - \
    && printf '%s  %s\n' "$RUN_LIMITER_SHA256" /opt/marb/run_limited.py | sha256sum -c - \
    && printf '%s  %s\n' "$WHEELHOUSE_MANIFEST_SHA256" /opt/marb/runtime-wheelhouse.sha256 | sha256sum -c - \
    && printf '%s  %s\n' "$NATIVE_DEB_LOCK_SHA256" /opt/marb/native-debs.lock.json | sha256sum -c - \
    && printf '%s  %s\n' "$NATIVE_DEB_MANIFEST_SHA256" /opt/marb/native-debs.sha256 | sha256sum -c - \
    && printf '%s  %s\n' "$NATIVE_BUNDLE_VERIFIER_SHA256" /opt/marb/verify_native_bundle.py | sha256sum -c - \
    && printf '%s  %s\n' "$BUILD_CONTEXT_MANIFEST_SHA256" /opt/marb/build-context.sha256 | sha256sum -c - \
    && printf '%s  %s\n' "2b5c4d9fef3189d95a6d8e550bb50f2cf7a7d0171000bd130c7bd18ecf9a7d80" /opt/marb/runtime-contract.json | sha256sum -c - \
    && printf '%s  %s\n' "64956f829563978bcfc229b43c773ab2d67828ca79808df71effa3e37b8c4e84" /opt/marb/cadclaw-calibration.json | sha256sum -c - \
    && cd /opt/marb \
    && /usr/local/bin/python3 -c "import os,re; from pathlib import Path; wheel_root=Path('/opt/marb/wheelhouse'); wheel_files=list(wheel_root.iterdir()); assert wheel_files and all(p.is_file() and not p.is_symlink() and p.suffix == '.whl' for p in wheel_files); wheel_lines=Path('/opt/marb/runtime-wheelhouse.sha256').read_text(encoding='ascii').splitlines(); wheel_matches=[re.fullmatch(r'([0-9a-f]{64})  (wheelhouse/[A-Za-z0-9][A-Za-z0-9._+-]*\.whl)', line) for line in wheel_lines]; assert wheel_lines and all(wheel_matches); wheel_entries={item.group(2):item.group(1) for item in wheel_matches}; wheel_paths=['wheelhouse/'+p.name for p in wheel_files]; assert len(wheel_entries) == len(wheel_lines) and sorted(wheel_entries) == sorted(wheel_paths); native_root=Path('/opt/marb/native-debs'); native_files=list(native_root.iterdir()); assert native_files and all(p.is_file() and not p.is_symlink() and p.suffix == '.deb' for p in native_files); native_lines=Path('/opt/marb/native-debs.sha256').read_text(encoding='ascii').splitlines(); native_matches=[re.fullmatch(r'([0-9a-f]{64})  (native-debs/[A-Za-z0-9][A-Za-z0-9.+~_-]*\.deb)', line) for line in native_lines]; assert native_lines and all(native_matches); native_entries={item.group(2):item.group(1) for item in native_matches}; native_paths=['native-debs/'+p.name for p in native_files]; assert len(native_entries) == len(native_lines) and sorted(native_entries) == sorted(native_paths); context_lines=Path('/opt/marb/build-context.sha256').read_text(encoding='ascii').splitlines(); context_matches=[re.fullmatch(r'([0-9a-f]{64})  (Dockerfile|\.dockerignore|requirements\.lock|run_limited\.py|runtime-contract\.v0\.13\.json|cadclaw-calibration\.fad0dd55\.json|wheelhouse\.sha256|native-debs\.lock\.json|verify_native_bundle\.py|native-debs\.sha256|wheelhouse/[A-Za-z0-9][A-Za-z0-9._+-]*\.whl|native-debs/[A-Za-z0-9][A-Za-z0-9.+~_-]*\.deb)', line) for line in context_lines]; assert context_lines and all(context_matches); context_entries={item.group(2):item.group(1) for item in context_matches}; assert len(context_entries) == len(context_lines) and [item.group(2) for item in context_matches] == sorted(context_entries); assert set(context_entries) == {'Dockerfile','.dockerignore','requirements.lock','run_limited.py','runtime-contract.v0.13.json','cadclaw-calibration.fad0dd55.json','wheelhouse.sha256','native-debs.lock.json','verify_native_bundle.py','native-debs.sha256',*wheel_entries,*native_entries}; assert context_entries['Dockerfile'] == os.environ['DOCKERFILE_SHA256']; assert context_entries['.dockerignore'] == os.environ['CONTEXT_DOCKERIGNORE_SHA256']; assert context_entries['requirements.lock'] == os.environ['REQUIREMENTS_LOCK_SHA256']; assert context_entries['run_limited.py'] == os.environ['RUN_LIMITER_SHA256']; assert context_entries['runtime-contract.v0.13.json'] == '2b5c4d9fef3189d95a6d8e550bb50f2cf7a7d0171000bd130c7bd18ecf9a7d80'; assert context_entries['cadclaw-calibration.fad0dd55.json'] == '64956f829563978bcfc229b43c773ab2d67828ca79808df71effa3e37b8c4e84'; assert context_entries['wheelhouse.sha256'] == os.environ['WHEELHOUSE_MANIFEST_SHA256']; assert context_entries['native-debs.lock.json'] == os.environ['NATIVE_DEB_LOCK_SHA256']; assert context_entries['verify_native_bundle.py'] == os.environ['NATIVE_BUNDLE_VERIFIER_SHA256']; assert context_entries['native-debs.sha256'] == os.environ['NATIVE_DEB_MANIFEST_SHA256']; assert all(context_entries[path] == digest for path,digest in wheel_entries.items()); assert all(context_entries[path] == digest for path,digest in native_entries.items()); assert context_entries['wheelhouse/cadclaw-0.10.0-py3-none-any.whl'] == os.environ['CADCLAW_WHEEL_SHA256']; cad=[p.name for p in wheel_files if p.name.split('-',1)[0].replace('_','-').lower() == 'cadclaw']; assert cad == ['cadclaw-0.10.0-py3-none-any.whl']" \
    && sha256sum -c runtime-wheelhouse.sha256 \
    && /usr/local/bin/python3 /opt/marb/verify_native_bundle.py archives --lock /opt/marb/native-debs.lock.json --bundle /opt/marb/native-debs --manifest /opt/marb/native-debs.sha256 --expected-base-image "$PYTHON_BASE_IMAGE" \
    && dpkg --unpack /opt/marb/native-debs/*.deb \
    && dpkg --configure --pending \
    && printf '%s  %s\n' "$CADCLAW_WHEEL_SHA256" /opt/marb/wheelhouse/cadclaw-0.10.0-py3-none-any.whl | sha256sum -c - \
    && /usr/local/bin/python3 -m pip install --disable-pip-version-check --no-cache-dir --no-index --no-deps --only-binary=:all: /opt/marb/wheelhouse/cadclaw-0.10.0-py3-none-any.whl \
    && /usr/local/bin/python3 -m pip install --disable-pip-version-check --no-cache-dir --no-index --only-binary=:all: --find-links=/opt/marb/wheelhouse --requirement /opt/marb/runtime-requirements.lock \
    && /usr/local/bin/python3 /opt/marb/verify_native_bundle.py runtime --lock /opt/marb/native-debs.lock.json --expected-base-image "$PYTHON_BASE_IMAGE" \
    && printf '%s\n' "{\"cadclaw_calibration_sha256\":\"64956f829563978bcfc229b43c773ab2d67828ca79808df71effa3e37b8c4e84\",\"cadclaw_commit\":\"fad0dd552a49a0b32336f1845c2b82873ad6360a\",\"cadclaw_gate_registry_version\":\"harness-gates.v1\",\"cadclaw_gate_spec_version\":\"0.13.0\",\"cadclaw_pin_basis\":\"marb_v0.13_calibrated_cadclaw_fad0dd55\",\"cadclaw_source_manifest_sha256\":\"6b6cfd465cd6b32cc90f7a8631601e63830f130553a2c593b45ab2d8302b6831\",\"cadclaw_version\":\"0.10.0\",\"cadquery_ocp_version\":\"7.8.1.1.post1\",\"cadquery_version\":\"2.7.0\",\"run_limiter_sha256\":\"$RUN_LIMITER_SHA256\",\"runtime_contract\":\"marb-v0.13-h2b\",\"runtime_contract_sha256\":\"2b5c4d9fef3189d95a6d8e550bb50f2cf7a7d0171000bd130c7bd18ecf9a7d80\"}" > /opt/marb/runtime.json \
    && /usr/local/bin/python3 -c "import base64,csv,hashlib,importlib.metadata as m,json,os,sys; from pathlib import Path; from cadclaw.gate_registry import HARNESS_GATE_REGISTRY; from cadclaw.gate_spec import GATE_SPEC_VERSION; contract_raw=Path('/opt/marb/runtime-contract.json').read_bytes(); calibration_raw=Path('/opt/marb/cadclaw-calibration.json').read_bytes(); contract=json.loads(contract_raw); calibration=json.loads(calibration_raw); candidate_manifest=calibration['source_manifests']['source_to_wheel_inputs']['candidate']; manifest_bytes=b''.join((item['sha256']+'  '+item['path']+'\n').encode('utf-8') for item in candidate_manifest['files']); assert calibration['classification'] == 'compatible' and calibration['failed_checks'] == []; assert calibration['source_control']['candidate_commit'] == 'fad0dd552a49a0b32336f1845c2b82873ad6360a'; assert len(candidate_manifest['files']) == candidate_manifest['file_count'] == 53; assert hashlib.sha256(manifest_bytes).hexdigest() == candidate_manifest['manifest_sha256'] == '6b6cfd465cd6b32cc90f7a8631601e63830f130553a2c593b45ab2d8302b6831'; expected={'runtime_contract':'marb-v0.13-h2b','runtime_contract_sha256':'2b5c4d9fef3189d95a6d8e550bb50f2cf7a7d0171000bd130c7bd18ecf9a7d80','cadclaw_version':'0.10.0','cadclaw_commit':'fad0dd552a49a0b32336f1845c2b82873ad6360a','cadclaw_gate_spec_version':'0.13.0','cadclaw_gate_registry_version':'harness-gates.v1','cadclaw_source_manifest_sha256':'6b6cfd465cd6b32cc90f7a8631601e63830f130553a2c593b45ab2d8302b6831','cadclaw_calibration_sha256':'64956f829563978bcfc229b43c773ab2d67828ca79808df71effa3e37b8c4e84','cadquery_version':'2.7.0','cadquery_ocp_version':'7.8.1.1.post1','cadclaw_pin_basis':'marb_v0.13_calibrated_cadclaw_fad0dd55','run_limiter_sha256':os.environ['RUN_LIMITER_SHA256']}; measured={'runtime_contract':contract['contract_id'],'runtime_contract_sha256':hashlib.sha256(contract_raw).hexdigest(),'cadclaw_version':m.version('cadclaw'),'cadclaw_commit':contract['cadclaw']['commit'],'cadclaw_gate_spec_version':GATE_SPEC_VERSION,'cadclaw_gate_registry_version':HARNESS_GATE_REGISTRY.version,'cadclaw_source_manifest_sha256':contract['cadclaw']['package_source_manifest_sha256'],'cadclaw_calibration_sha256':hashlib.sha256(calibration_raw).hexdigest(),'cadquery_version':m.version('cadquery'),'cadquery_ocp_version':m.version('cadquery-ocp'),'cadclaw_pin_basis':contract['pin_basis'],'run_limiter_sha256':hashlib.sha256(Path('/opt/marb/run_limited.py').read_bytes()).hexdigest()}; assert sys.version_info[:2] == (3, 11); assert json.loads(Path('/opt/marb/runtime.json').read_text()) == expected == measured; assert contract['cadclaw']['calibration_evidence_sha256'] == measured['cadclaw_calibration_sha256']; roots=('cadclaw/','cadclaw_cli/','cadclaw_mcp/','cadharness/'); expected_files={item['path']:item['sha256'] for item in candidate_manifest['files'] if item['path'].startswith(roots) and item['path'].endswith('.py')}; dist=m.distribution('cadclaw'); installed=sorted(str(item).replace(chr(92),'/') for item in (dist.files or []) if str(item).replace(chr(92),'/').startswith(roots) and str(item).replace(chr(92),'/').endswith('.py')); assert installed == sorted(expected_files); assert all(hashlib.sha256(Path(dist.locate_file(path)).read_bytes()).hexdigest() == digest for path,digest in expected_files.items()); direct=json.loads(dist.read_text('direct_url.json')); archive=direct['archive_info']; observed=archive.get('hashes',{}).get('sha256') or archive.get('hash','').removeprefix('sha256='); assert observed == os.environ['CADCLAW_WHEEL_SHA256']; rows=list(csv.reader(dist.read_text('RECORD').splitlines())); assert rows and all((not spec and (rel.endswith('.pyc') or rel.endswith('/RECORD'))) or (spec.startswith('sha256=') and hashlib.sha256(Path(dist.locate_file(rel)).read_bytes()).digest() == base64.urlsafe_b64decode(spec.split('=',1)[1]+'===')) for rel,spec,size in rows); import cadclaw,cadquery,OCP,vtk" \
    && /usr/local/bin/python3 -c "import json,os; from pathlib import Path; keys={'base_image':'PYTHON_BASE_IMAGE','requirements_lock_sha256':'REQUIREMENTS_LOCK_SHA256','wheelhouse_manifest_sha256':'WHEELHOUSE_MANIFEST_SHA256','cadclaw_wheel_sha256':'CADCLAW_WHEEL_SHA256','run_limiter_sha256':'RUN_LIMITER_SHA256','native_deb_lock_sha256':'NATIVE_DEB_LOCK_SHA256','native_deb_manifest_sha256':'NATIVE_DEB_MANIFEST_SHA256','native_bundle_verifier_sha256':'NATIVE_BUNDLE_VERIFIER_SHA256','dockerfile_sha256':'DOCKERFILE_SHA256','context_dockerignore_sha256':'CONTEXT_DOCKERIGNORE_SHA256','build_context_manifest_sha256':'BUILD_CONTEXT_MANIFEST_SHA256'}; value={'schema':'marb_h2b_image_build_provenance.v3','runtime_contract':'marb-v0.13-h2b','runtime_contract_sha256':'2b5c4d9fef3189d95a6d8e550bb50f2cf7a7d0171000bd130c7bd18ecf9a7d80','cadclaw_commit':'fad0dd552a49a0b32336f1845c2b82873ad6360a','cadclaw_gate_spec_version':'0.13.0','cadclaw_gate_registry_version':'harness-gates.v1','cadclaw_pin_basis':'marb_v0.13_calibrated_cadclaw_fad0dd55','cadclaw_source_manifest_sha256':'6b6cfd465cd6b32cc90f7a8631601e63830f130553a2c593b45ab2d8302b6831','cadclaw_calibration_sha256':'64956f829563978bcfc229b43c773ab2d67828ca79808df71effa3e37b8c4e84','native_deb_package_count':39,'native_deb_total_bytes':48570480,**{key:os.environ[name] for key,name in keys.items()}}; Path('/opt/marb/build-provenance.json').write_text(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n',encoding='ascii')" \
    && chmod 0555 /opt/marb/run_limited.py \
    && chmod 0444 /opt/marb/runtime.json /opt/marb/runtime-contract.json /opt/marb/cadclaw-calibration.json /opt/marb/build-provenance.json /opt/marb/runtime-requirements.lock /opt/marb/runtime-wheelhouse.sha256 /opt/marb/native-debs.lock.json /opt/marb/native-debs.sha256 /opt/marb/verify_native_bundle.py /opt/marb/build-context.sha256 \
    && rm -rf /opt/marb/wheelhouse /opt/marb/native-debs /root/.cache

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
