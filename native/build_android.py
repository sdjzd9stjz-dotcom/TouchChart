"""Build and sign a native Java APK using official SDK tools, without Gradle."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parent
TOOLS=ROOT/'.tools/ready'
JDK=next((TOOLS/'jdk').glob('*/bin/javac.exe')).parent
BUILD=next((TOOLS/'build-tools-35.0.0').rglob('aapt2.exe')).parent
ANDROID_SOURCE=next((TOOLS/'platforms-android-35').rglob('android.jar'))
# AAPT2 on Windows cannot consistently open Unicode paths; stage inputs in ASCII TEMP.
OUT=Path(tempfile.mkdtemp(prefix='touchchart-android-build-'))
ANDROID=OUT/'android.jar';shutil.copy2(ANDROID_SOURCE,ANDROID)
SOURCE=OUT/'source';shutil.copytree(ROOT/'android',SOURCE)
DIST=ROOT/'dist';DIST.mkdir(exist_ok=True)
ART=ROOT/'artifacts';ART.mkdir(exist_ok=True)
log=(ART/'android-build.txt').open('w',encoding='utf-8')
env=os.environ.copy();env['JAVA_HOME']=str(JDK.parent);env['PATH']=str(JDK)+os.pathsep+env.get('PATH','')
def run(args,private=False):
    if not private:log.write('COMMAND '+subprocess.list2cmdline(list(map(str,args)))+'\n');log.flush()
    r=subprocess.run(list(map(str,args)),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    log.write(r.stdout.decode('utf-8',errors='replace'));log.write(f'EXIT CODE: {r.returncode}\n');log.flush()
    if r.returncode:raise RuntimeError('Build failed; see '+str(ART/'android-build.txt'))
    return r.stdout
run([BUILD/'aapt2.exe','compile','--dir',SOURCE/'res','-o',OUT/'resources.zip'])
unsigned=OUT/'unsigned.apk'
run([BUILD/'aapt2.exe','link','-o',unsigned,'--manifest',SOURCE/'AndroidManifest.xml','-I',ANDROID,OUT/'resources.zip','--min-sdk-version','26','--target-sdk-version','35'])
classes=OUT/'classes';classes.mkdir(exist_ok=True)
sources=list((SOURCE/'src').rglob('*.java'))
run([JDK/'javac.exe','-encoding','UTF-8','--release','8','-classpath',ANDROID,'-d',classes,*sources])
dex=OUT/'dex';dex.mkdir(exist_ok=True)
run([JDK/'java.exe','-cp',BUILD/'lib/d8.jar','com.android.tools.r8.D8','--min-api','26','--lib',ANDROID,'--output',dex,*classes.rglob('*.class')])
with zipfile.ZipFile(unsigned,'a',zipfile.ZIP_DEFLATED) as z:
    for p in dex.glob('*.dex'):z.write(p,p.name)
aligned=OUT/'aligned.apk';run([BUILD/'zipalign.exe','-f','-p','4',unsigned,aligned])
signing=ROOT/'.signing';signing.mkdir(exist_ok=True)
password_file=signing/'password.txt'
if not password_file.exists():password_file.write_text(secrets.token_urlsafe(32),encoding='ascii')
env['TOUCHCHART_SIGN_PASSWORD']=password_file.read_text(encoding='ascii').strip()
key=signing/'touchchart.jks'
if not key.exists():run([JDK/'keytool.exe','-genkeypair','-keystore',key,'-storepass:env','TOUCHCHART_SIGN_PASSWORD','-keypass:env','TOUCHCHART_SIGN_PASSWORD','-alias','touchchart','-keyalg','RSA','-keysize','3072','-validity','10950','-dname','CN=TouchChart Local,OU=Personal Software,O=TouchChart,C=CN'],True)
apk=OUT/'TouchChart-Android.apk'
run([JDK/'java.exe','-jar',BUILD/'lib/apksigner.jar','sign','--ks',key,'--ks-key-alias','touchchart','--ks-pass','env:TOUCHCHART_SIGN_PASSWORD','--key-pass','env:TOUCHCHART_SIGN_PASSWORD','--out',apk,aligned])
verified=run([JDK/'java.exe','-jar',BUILD/'lib/apksigner.jar','verify','--verbose','--print-certs',apk])
(ART/'apk-signature.txt').write_bytes(verified)
badging=run([BUILD/'aapt2.exe','dump','badging',apk]);(ART/'apk-manifest.txt').write_bytes(badging)
run([BUILD/'zipalign.exe','-c','-v','4',apk])
with zipfile.ZipFile(apk) as z:assert z.testzip() is None and 'classes.dex' in z.namelist()
shutil.copy2(apk,DIST/apk.name);apk=DIST/apk.name
(ART/'apk-build-result.json').write_text(json.dumps({'apk':str(apk),'bytes':apk.stat().st_size,'sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'signed':True,'zipCRC':'PASS','minSdk':26,'targetSdk':35,'physicalDeviceTest':'NOT RUN'},indent=2),encoding='utf-8')
log.close();print('SIGNED APK BUILT AND VERIFIED:',apk)
