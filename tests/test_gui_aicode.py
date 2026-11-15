import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))
from patcher.agy import aicode as native
from patcher.ide import aicode as frontend


class GuiAICodeTests(unittest.TestCase):
    def test_pristine_frontend_uses_personal_path_without_legacy_onboarding(self):
        data = ('class Example{async confirmUserForService(t){if('
                + frontend.PERSONAL_GATE + '){return "personal"}}'
                + frontend.START + 'let r=await this.makePostRequest("v1internal:loadCodeAssist");'
                'const i=r.paidTier?.availableCredits;return r}' + frontend.END + '}}').encode()
        with patch.object(frontend, "PRISTINE_SOURCE_SHA256", native.digest(data)):
            result = frontend.patch_frontend(data).decode()
        self.assertNotIn(frontend.PERSONAL_GATE, result)
        self.assertIn("!t.isGcpTos||this._productService.isGoogleInternal", result)
        self.assertIn("fetchLicenses", result)

    def test_profiles_refuse_other_builds(self):
        for profile in ("cli", "hub", "manager", "unknown"):
            with self.assertRaises(ValueError):
                native.patch_native(b"unrecognized binary", profile=profile)
        with self.assertRaises(ValueError):
            frontend.patch_frontend(b"unrecognized frontend")

    def test_manager_fixture(self):
        if os.name != "nt":
            self.skipTest("Optional local Windows fixture")
        source = Path(os.environ["LOCALAPPDATA"]) / "Programs/antigravity/resources/bin/language_server.exe"
        if source.exists() and native.digest(source.read_bytes()) not in native.MANAGER_SOURCE_HASHES:
            source = source.with_name(source.name + ".before-aicode")
        if not source.exists() or native.digest(source.read_bytes()) not in native.MANAGER_SOURCE_HASHES:
            self.skipTest("Audited original Manager fixture is not present")
        data = source.read_bytes()
        result = native.patch_native(data, profile="manager")
        self.assertEqual(len(data), len(result))
        for at, before, _ in native.MANAGER_PATCHES:
            length = len(bytes.fromhex(before))
            result = result[:at] + data[at:at + length] + result[at + length:]
        self.assertEqual(result, data)

    @unittest.skipUnless(shutil.which("node"), "Node required to execute frontend branch tests")
    def test_frontend_uses_real_license_and_preserves_enterprise_path(self):
        original = (frontend.START + 'let r=await this.makePostRequest("v1internal:loadCodeAssist");'
                    'const i=r.paidTier?.availableCredits;return r}' + frontend.END + '}')
        with patch.object(frontend, "SOURCE_SHA256", native.digest(original.encode())):
            result = frontend.patch_frontend(original.encode()).decode()
        body = result[len(frontend.START):result.index(frontend.END)].removesuffix("}")
        program = r'''
const assert=require('node:assert/strict');
const body=JSON.parse(process.argv[1]);
let response={licenses:[{userTier:'free-tier',tierDisplayName:'Starter'}]}, calls=[];
const token={accessToken:'fixture-token'};
const DKe=(t)=>{assert.equal(t,token);return {request:async r=>{calls.push(r);return {data:response}}}};
const fn=new Function('DKe','VAe','QP','Fre','return async function(t,n){'+body+'}')(DKe,x=>x,()=>({fromJson:x=>x}),{});
const context={_unifiedStateSyncService:{},userAgent:'test',getOAuthTokenFromStateSyncIfUnexpired:async()=>token,
makePostRequest:async path=>{calls.push({path});return {paidTier:{id:'enterprise'}}}};
(async()=>{
 let r=await fn.call(context,token);assert.equal(r.currentTier.id,'free-tier');assert.equal(r.paidTier.id,'free-tier');
 assert.equal(calls[0].url,'https://aicode.googleapis.com/v1alpha:fetchLicenses');assert.equal(calls[0].method,'GET');
 response={licenses:[]};await assert.rejects(()=>fn.call(context,token),/no license/);
 response={licenses:[{userTier:'pro-tier'}]};r=await fn.call(context,token);assert.equal(r.currentTier.id,'pro-tier');
 calls=[];r=await fn.call(context,{isGcpTos:true});assert.equal(r.paidTier.id,'enterprise');assert.deepEqual(calls,[{path:'v1internal:loadCodeAssist'}]);
 console.log('OK');
})().catch(e=>{console.error(e);process.exit(1)});
'''
        process = subprocess.run([shutil.which("node"), "-e", program, json.dumps(body)],
                                 capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "OK")


if __name__ == "__main__":
    unittest.main()
