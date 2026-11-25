"""LifeLink document workflow against REAL MinIO, incl. outage + recovery."""
import httpx, subprocess, sys, time, os, signal, datetime
API="http://127.0.0.1:8300"; O={"Origin":"http://127.0.0.1:4400"}; PW="LifeLink-Demo-2026!"
PDF=b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
def login(e):
    c=httpx.Client(base_url=API,headers=O,timeout=30); r=c.post("/auth/login",json={"email":e,"password":PW}); assert r.status_code==200,r.text; return c
print("# LifeLink document workflow on REAL MinIO --", datetime.datetime.now(datetime.UTC).isoformat())
hosp=login("demo.hospital@lifelink-demo.example.com"); admin=login("demo.admin@lifelink-demo.example.com")
minio_pid=sys.argv[1]
print("1. stop MinIO (simulated outage)"); os.kill(int(minio_pid), signal.SIGTERM); time.sleep(3)
r=hosp.post("/verifications",data={"kind":"HOSPITAL_FACILITY"},files=[("files",("licence.pdf",PDF,"application/pdf"))])
print("   upload during outage ->", r.status_code, r.json().get("detail"))
mine=hosp.get("/verifications/me").json(); print("   verification requests stored during outage:", len(mine))
print("2. restart MinIO"); 
p=subprocess.Popen([f"/srv/realsvc/minio-root/usr/local/bin/minio","server","/srv/realsvc/minio-data","--address","127.0.0.1:9000","--console-address","127.0.0.1:9001"],stdout=open("/srv/realsvc/logs/minio2.log","w"),stderr=subprocess.STDOUT,start_new_session=True)
for _ in range(30):
    try:
        if httpx.get("http://127.0.0.1:9000/minio/health/ready",timeout=2).status_code==200: break
    except Exception: pass
    time.sleep(1)
print("   new MinIO pid", p.pid)
r=hosp.post("/verifications",data={"kind":"HOSPITAL_FACILITY"},files=[("files",("licence.pdf",PDF,"application/pdf"))])
print("   upload after recovery ->", r.status_code); vid=r.json()["id"]
print("   document scan status:", [d.get("scan_status") for d in r.json().get("documents",[])] or "(not exposed in response)")
d=admin.get(f"/admin/verifications/{vid}/documents/0")
print("3. admin downloads the stored document through LifeLink (read back from MinIO) ->", d.status_code, "| bytes identical:", d.content==PDF, "| content-type:", d.headers.get("content-type"))
open("/srv/realsvc/minio.pid","w").write(str(p.pid))
