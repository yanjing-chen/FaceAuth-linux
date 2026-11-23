//! FaceAuth daemon: no desktop toolkit. Trusted Python camera+ML workers are children
//! of this daemon, never injected into PAM. No PAM service is edited by this binary.
use faceauth_core::{config::Config,peer,protocol,scene::{self,SceneManager},store::{Face,FaceStore},infer::Worker};
use std::{env,fs,io::{self,Read,Write},os::unix::{fs::{PermissionsExt,MetadataExt,FileTypeExt},net::{UnixListener,UnixStream}},path::{Path,PathBuf},sync::{Arc,Mutex,atomic::{AtomicUsize,Ordering}},thread,time::{Duration,Instant},process::{Command,Stdio}};
unsafe extern "C"{fn geteuid()->u32;}
static SCENE_WRITE_LOCK: Mutex<()> = Mutex::new(());

struct Runtime {dev:bool,script_dir:PathBuf,store_dir:PathBuf, scene_dir:PathBuf}
struct Pipeline {capture:Option<Worker>,infer:Option<Worker>,opened_for:Option<String>,preview_owner:Option<String>,last_preview:Option<Instant>}
struct Snap{w:usize,h:usize,gray:String,ir_w:usize,ir_h:usize,ir_gray:String}

fn worker_error(text:&str)->String {if text.starts_with("ERR"){text.into()}else{"ERR device or inference unavailable".into()}}
fn hex_nibble(b:u8)->Option<u8>{match b{b'0'..=b'9'=>Some(b-b'0'),b'a'..=b'f'=>Some(b-b'a'+10),b'A'..=b'F'=>Some(b-b'A'+10),_=>None}}
fn parse_snap(text:&str)->Result<Snap,String>{
    let mut parts=text.splitn(7,' ');
    if parts.next()!=Some("OK"){return Err(worker_error(text));}
    let read_usize=|s:Option<&str>| -> Result<usize,String> {s.ok_or("short snapshot")?.parse().map_err(|_|"bad frame dimensions".into())};
    let w=read_usize(parts.next())?;let h=read_usize(parts.next())?;let gray=parts.next().ok_or("missing pixels")?.to_owned();
    let ir_w=read_usize(parts.next())?;let ir_h=read_usize(parts.next())?;let ir_gray=parts.next().ok_or("missing IR")?.to_owned();
    if !(1..=800).contains(&w)||!(1..=600).contains(&h)||gray.len()!=w*h*2||!gray.bytes().all(|b|b.is_ascii_hexdigit()){return Err("bad gray frame".into());}
    if ir_w*ir_h>800*600||(ir_w>0 && ir_gray.len()!=ir_w*ir_h*2){return Err("bad IR frame".into());}
    Ok(Snap{w,h,gray,ir_w,ir_h,ir_gray})
}
fn parse_feat(answer:&str)->Result<[f32;128],String>{
    if !answer.starts_with("OK "){return Err("face not detected".into());}
    let v:Vec<f32>=answer[3..].split_whitespace().map(|x|x.parse::<f32>().unwrap_or(f32::NAN)).collect();
    if v.len()!=128||v.iter().any(|f|!f.is_finite()){return Err("invalid embedding from engine".into());}
    let mut a=[0.0f32;128];a.copy_from_slice(&v);
    let norm=a.iter().map(|x|(*x as f64)*(*x as f64)).sum::<f64>().sqrt();if norm<0.01{return Err("degenerate embedding".into());}
    for v in &mut a{*v=(*v as f64/norm)as f32;}Ok(a)
}
impl Pipeline {
    fn new()->Self{Self{capture:None,infer:None,opened_for:None,preview_owner:None,last_preview:None}}
    fn close_camera(&mut self){
        // Dropping the worker closes its V4L2 descriptors even when a prior
        // request failed. No GUI process needs to own these handles.
        self.capture=None;
        self.opened_for=None;
    }
    fn stop_preview(&mut self){
        self.preview_owner=None;
        self.last_preview=None;
        self.close_camera();
    }
    fn start_preview(&mut self,user:&str){
        self.stop_preview();
        self.preview_owner=Some(user.to_owned());
        self.last_preview=Some(Instant::now());
    }
    fn expire_preview(&mut self){
        if self.last_preview.is_some_and(|instant|instant.elapsed()>Duration::from_secs(3)){
            self.stop_preview();
        }
    }
    fn camera(&mut self,rt:&Runtime,cfg:&Config)->Result<&mut Worker,String>{
        let key=format!("{}:{}:{}",cfg.mode,cfg.rgb_device,cfg.ir_device);
        if self.opened_for.as_ref()!=Some(&key) || self.capture.is_none(){
            self.capture=None;self.opened_for=None;
            let script=rt.script_dir.join("capture_bridge.py");
            let mut worker=Worker::launch(script.to_str().ok_or("bad capture script path")?).map_err(|e|e.to_string())?;
            worker.ping().map_err(|_|"camera worker not ready")?;
            let rgb=if cfg.rgb_device.is_empty(){"-"}else{&cfg.rgb_device};
            let ir=if cfg.ir_device.is_empty(){"-"}else{&cfg.ir_device};
            // Paths are checked again by helper; never accept whitespace into line protocol.
            if rgb.contains(char::is_whitespace)||ir.contains(char::is_whitespace){return Err("invalid camera device path".into());}
            let cmd=format!("OPEN {} {} {}",cfg.mode,rgb,ir);
            let reply=worker.query(&cmd).map_err(|e|e.to_string())?;
            if reply!="OK"{return Err(worker_error(&reply));}
            self.capture=Some(worker);self.opened_for=Some(key);
        }
        Ok(self.capture.as_mut().unwrap())
    }
    fn engine(&mut self,rt:&Runtime)->Result<&mut Worker,String>{
        if self.infer.is_none(){
            let script=rt.script_dir.join("infer_engine.py");
            let mut w=Worker::launch(script.to_str().ok_or("bad infer script path")?).map_err(|e|e.to_string())?;
            w.ping().map_err(|e|format!("infer not ready: {e}"))?;
            self.infer=Some(w);
        }Ok(self.infer.as_mut().unwrap())
    }
    fn snap(&mut self,rt:&Runtime,c:&Config)->Result<Snap,String>{
        let response=match self.camera(rt,c)?.query("SNAP") {
            Ok(v)=>v,
            Err(e)=>{self.capture=None;self.opened_for=None;return Err(e.to_string());}
        };
        parse_snap(&response)
    }
    fn embedding(&mut self,rt:&Runtime,s:&Snap)->Result<[f32;128],String>{
        let response=match self.engine(rt)?.query(&format!("EMBED {} {} {}",s.w,s.h,s.gray)){
            Ok(v)=>v, Err(e)=>{self.infer=None;return Err(e.to_string());}
        };
        parse_feat(&response)
    }
    fn live(&mut self,rt:&Runtime,cfg:&Config,s:&Snap)->Result<bool,String>{
        if cfg.liveness=="none"{return Ok(true);}
        let (w,h,gray)=if cfg.mode=="dual"{
            if s.ir_w==0 || s.ir_h==0{return Err("IR required".into());}
            (s.ir_w,s.ir_h,&s.ir_gray)
        }else{(s.w,s.h,&s.gray)};
        // 暗 IR 帧跳过：补光闪烁会产生全黑帧，直接拒绝该帧让多帧逻辑重取。
        // 用采样字节估算亮度，避免逐像素解析整个 hex（性能）。
        if cfg.dark_ir_skip && cfg.mode=="dual" {
            let bytes=gray.as_bytes();
            let step=(bytes.len()/256).max(2);
            let mut sum=0u64;let mut n=0u64;
            let mut i=0usize;
            while i+1<bytes.len(){
                if let (Some(a),Some(b))=(hex_nibble(bytes[i]),hex_nibble(bytes[i+1])){
                    sum+=(a*16+b) as u64;n+=1;
                }
                i+=step*2;
            }
            if n>0 && sum/n < 12 {return Err("dark IR frame skipped".into());}
        }
        let result=match self.engine(rt)?.query(&format!("LIVENESS {w} {h} {gray}")){
            Ok(v)=>v,Err(e)=>{self.infer=None;return Err(e.to_string());}
        };
        let score=result.strip_prefix("OK ").ok_or("liveness failed")?.parse::<f32>().map_err(|_|"invalid liveness")?;
        Ok(score.is_finite()&&score>=0.5)
    }
    fn preview(&mut self,rt:&Runtime,cfg:&Config,user:&str)->Result<String,String>{
        self.expire_preview();
        if self.preview_owner.as_deref()!=Some(user){return Err("preview session not active".into());}
        self.last_preview=Some(Instant::now());
        let result=match self.camera(rt,cfg)?.query("PREVIEW"){
            Ok(v)=>v,Err(e)=>{self.capture=None;self.opened_for=None;return Err(e.to_string());}
        };
        if !result.starts_with("OK "){return Err(worker_error(&result));}
        if result.len()>500000||!result[3..].bytes().all(|b|b.is_ascii_hexdigit()){return Err("bad preview".into());}
        Ok(result)
    }
    fn enroll_capture(&mut self,rt:&Runtime,cfg:&Config)->Result<[f32;128],String>{
        // Security: liveness per collected frame; no preview frame is trusted implicitly.
        let mut average=[0.0f64;128];let deadline=Instant::now()+Duration::from_secs(20);
        let mut accepted=0;
        while accepted<5 && Instant::now()<deadline {
            let snap=self.snap(rt,cfg)?;
            if !self.live(rt,cfg,&snap)?{continue;}
            if let Ok(embedding)=self.embedding(rt,&snap){for (a,x) in average.iter_mut().zip(embedding){*a+=x as f64;}accepted+=1;}
        }
        if accepted<5{return Err("unable to obtain five valid live face samples".into());}
        let norm=average.iter().map(|v|v*v).sum::<f64>().sqrt();if norm<0.01{return Err("invalid mean vector".into());}
        let mut result=[0f32;128];for (x,a) in result.iter_mut().zip(average){*x=(a/norm)as f32;}Ok(result)
    }
    fn authenticate(&mut self,rt:&Runtime,cfg:&Config,store:&FaceStore)->String{
        if store.faces.is_empty(){return "IGNORE no-template".into();}
        // 多帧择优：单帧取景受姿态/曝光/瞬态遮挡影响会偶发低相似度，
        // 采多帧取最高相似度能显著提升通过率（录入侧本就是 5 帧平均）。
        // 每帧仍独立做完整判定（比对 + 活体），任一帧通过即接受。
        // 快速成功：首帧通过则立即返回，不增加正常路径的认证耗时。
        let auth_frames=cfg.effective_auth_frames();
        let mut best:(String,f32,bool)=(String::new(),-1.0,false);
        let mut saw_frame=false;
        for _ in 0..auth_frames {
            // Any worker/camera/security error on this frame is skipped; if no
            // frame ever succeeds we fall back to IGNORE (password), never OK.
            let result=(||->Result<(String,f32,bool),String>{
                let s=self.snap(rt,cfg)?;
                let embed=self.embedding(rt,&s)?;
                let (label,sim)=store.best_match(&embed).ok_or("no enrollment")?;
                if sim<cfg.threshold{return Ok((label.into(),sim,false));}
                let live=self.live(rt,cfg,&s)?;
                Ok((label.into(),sim,live))
            })();
            match result {
                Ok((label,sim,live))=>{
                    saw_frame=true;
                    if sim>best.1{best=(label.clone(),sim,live);}
                    if sim>=cfg.threshold && live {
                        return format!("OK mode={} sim={sim:.3} face={label} result=accepted",cfg.mode);
                    }
                }
                Err(_)=>{ /* 单帧故障：继续尝试下一帧 */ }
            }
        }
        if !saw_frame {return "IGNORE unavailable-or-processing-error".into();}
        let (label,sim,live)=best;
        if sim>=cfg.threshold && !live {
            format!("DENIED mode={} sim={sim:.3} face={label} result=liveness",cfg.mode)
        }else{
            format!("DENIED mode={} sim={sim:.3} face={label} result=mismatch",cfg.mode)
        }
    }
}
fn system_config(_rt:&Runtime,user:&str)->Result<Config,String>{
    let uid=peer::user_uid(user).ok_or("user not found")?;
    let home=peer::home_dir(user).ok_or("home not found")?;
    let path=Path::new(&home).join(".config/faceauth-rebuild/config");
    let meta=fs::symlink_metadata(&path).map_err(|e|e.to_string())?;
    if meta.file_type().is_symlink()||!meta.is_file(){return Err("invalid config file".into());}
    use std::os::unix::fs::MetadataExt;
    if meta.uid()!=uid || meta.mode()&0o077!=0 {return Err("config must be owner-only 0600".into());}
    Config::load(&path)
}
fn store_path(rt:&Runtime,user:&str)->Result<PathBuf,String>{
    let id=peer::user_uid(user).ok_or("unknown user")?;
    Ok(rt.store_dir.join(format!("{id}.bin")))
}
fn is_self(uid:u32,user:&str)->bool{peer::user_uid(user)==Some(uid)}
// The privileged scene manager is intentionally separate from capture/inference.
// Authorization never occurs while holding the camera pipeline mutex.
fn scene_manager(rt:&Runtime)->SceneManager {
    if rt.dev{SceneManager::sandbox(&rt.scene_dir)}else{SceneManager::production()}
}
fn scene_list(rt:&Runtime)->String{
    let mgr=scene_manager(rt);
    let mut values=Vec::new();
    for service in scene::SERVICES {
        match mgr.status(service){
            Ok(active)=>values.push(format!("{service}:{}",if active{1}else{0})),
            Err(_)=>values.push(format!("{service}:?"))
        }
    }
    format!("OK {}",values.join(","))
}
// Linux /proc/pid/stat field 22 is process starttime. Obtain a stable
// (pid,starttime,uid) identity from the Unix socket's SO_PEERCRED, not an
// untrusted client argument. Check the same identity after polkit returns.
fn proc_starttime(pid:i32)->Result<u64,String>{
    if pid<=0 {return Err("missing peer process".into());}
    let raw=fs::read_to_string(format!("/proc/{pid}/stat")).map_err(|_|"peer process disappeared")?;
    let end=raw.rfind(')').ok_or("invalid peer process stat")?;
    raw[end+1..].split_whitespace().nth(19).ok_or("missing process starttime")?
        .parse::<u64>().map_err(|_|"invalid process starttime".into())
}
fn authorize_action(rt:&Runtime, uid:u32, pid:i32, action:&str)->Result<(),String>{
    if rt.dev {return Ok(());}
    if uid==0{return Ok(());}
    let before=proc_starttime(pid)?;
    let identity=format!("{pid},{before},{uid}");
    // 用 pkcheck 查询调用者是否已获授权。
    // 关键：pkcheck 可能退出码 0 但 stdout 打印 `polkit.result=auth_admin`，
    // 这表示"此动作需要管理员认证"而不是"已授权"。必须拒绝这种结果，
    // 否则任何进程都能绕过授权（实测确认过这个坑）。
    let mut child=Command::new("/usr/bin/pkcheck")
        .args(["--action-id",action, "--process",&identity])
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null())
        .spawn().map_err(|_|"polkit authorization unavailable".to_string())?;
    let mut output=String::new();
    if let Some(mut out)=child.stdout.take(){let _=out.read_to_string(&mut output);}
    let status=child.wait().map_err(|_|"polkit authorization failed".to_string())?;
    let needs_auth=["auth_admin","auth_self","auth_admin_keep","auth_self_keep"]
        .iter().any(|token|output.contains(token));
    if !status.success()||needs_auth{
        return Err("administrator authorization required but not granted".into());
    }
    if proc_starttime(pid)?!=before{return Err("peer process identity changed".into());}
    Ok(())
}
fn authorize_scene(rt:&Runtime,uid:u32,pid:i32)->Result<(),String>{
    authorize_action(rt,uid,pid,"org.faceauth.rebuild.manage-scenes")
}
/// 录入/删除人脸需要管理员授权，防止他人在同一会话中偷偷登记或删除人脸。
fn authorize_models(rt:&Runtime,uid:u32,pid:i32)->Result<(),String>{
    authorize_action(rt,uid,pid,"org.faceauth.rebuild.manage-models")
}
fn handle(rt:&Runtime,pipe:&Mutex<Pipeline>,uid:u32,pid:i32,input:&str)->String {
    let p:Vec<&str>=input.split_whitespace().collect();
    if p.is_empty(){return "ERR empty command".into();}
    if p[0]=="PING"&&p.len()==1{return "PONG".into();}
    if p.len()<2{return "ERR missing user".into();}
    let user=p[1];
    let auth=p[0]=="AUTH";
    if auth {if uid!=0{return "ERR root-only AUTH".into();}}
    else if !(is_self(uid,user)||(uid==0 && matches!(p[0],"SCENE_ON"|"SCENE_OFF"|"SCENE_LIST"))) {
        return "ERR peer uid mismatch".into();
    }
    if peer::user_uid(user).is_none(){return "ERR invalid user".into();}
    let path=match store_path(rt,user){Ok(p)=>p,Err(_)=>return "ERR store path".into()};
    match p[0]{
        "SCENE_LIST" if p.len()==2=>scene_list(rt),
        "SCENE_ON"|"SCENE_OFF" if p.len()==3=>{
            if scene::allowed_service(p[2]).is_none(){return "ERR service not on allowlist".into();}
            match authorize_scene(rt,uid,pid) {
                Err(e)=>format!("ERR {e}"),
                Ok(())=>match SCENE_WRITE_LOCK.lock(){
                    Ok(_guard)=>match scene_manager(rt).set(p[2],p[0]=="SCENE_ON"){
                        Ok(result)=>result, Err(e)=>format!("ERR {e}")
                    },
                    Err(_)=>"ERR PAM scene lock unavailable".into(),
                }
            }
        },
        "LIST" if p.len()==2=>match FaceStore::load(&path){
            Ok(s) if s.faces.is_empty()=>"OK 0".into(),
            Ok(s)=>format!("OK {} {}",s.faces.len(),s.faces.iter().map(|f|f.label.as_str()).collect::<Vec<_>>().join(",")),
            Err(_)=>"ERR template unreadable or damaged".into()},
        "DELETE" if p.len()==2=>{
            if let Err(e)=authorize_models(rt,uid,pid){return format!("ERR {e}");}
            match fs::remove_file(path){Ok(_)=>"OK deleted".into(),Err(ref e)if e.kind()==io::ErrorKind::NotFound=>"OK no model".into(),Err(_)=>"ERR delete failed".into()}
        },
        "DELETE_FACE" if p.len()==3=>{
            if let Err(e)=authorize_models(rt,uid,pid){return format!("ERR {e}");}
            match FaceStore::load(&path){Ok(mut st)=>{if !st.remove(p[2]){"OK no such face".into()}
                else{match st.save(&path){Ok(_)=>"OK deleted".into(),Err(_)=>"ERR unable to write template".into()}}},Err(_)=>"ERR template damaged".into()}
        },
        "ENROLL"|"ENROLL_AS" if (p[0]=="ENROLL"&&p.len()==3)||(p[0]=="ENROLL_AS"&&p.len()==4)=>{
            if let Err(e)=authorize_models(rt,uid,pid){return format!("ERR {e}");}
            let (label,hex)=if p[0]=="ENROLL"{("default",p[2])}else{(p[2],p[3])};
            if protocol::normalize_label(label).is_none(){return "ERR invalid label".into();}
            let e=match protocol::decode_embedding(hex){Ok(e)=>e,Err(_)=>return "ERR bad embedding".into()};
            let mut st=match FaceStore::load(&path){Ok(st)=>st,Err(_)=>return "ERR template damaged".into()};
            if st.put(Face{label:label.into(),embedding:e}).is_err(){return "ERR cannot add face".into();}
            match st.save(&path){Ok(_)=>format!("OK enrolled:{label}"),Err(_)=>"ERR unable to store template".into()}
        },
        "PREVIEW_START" if p.len()==2=>match pipe.lock(){
            Ok(mut x)=>{x.start_preview(user);"OK preview started".into()},
            Err(_)=>"ERR worker lock".into()
        },
        "PREVIEW_STOP" if p.len()==2=>match pipe.lock(){
            Ok(mut x) if x.preview_owner.as_deref()==Some(user)=>{
                x.stop_preview();"OK camera released".into()
            },
            Ok(_)=>"OK preview not active".into(),
            Err(_)=>"ERR worker lock".into()
        },
        "DEVICE_TYPES" if p.len()==2=>{
            let cfg=match system_config(rt,user){
                Ok(c)=>c,Err(_)=>return "ERR configure a camera first".into()
            };
            match pipe.lock(){
                // Never reopen a camera for an outdated background probe after
                // recording/test has disabled the preview session.
                Ok(mut x) if x.preview_owner.as_deref()==Some(user)=>match x.camera(rt,&cfg){
                    Ok(worker)=>match worker.query("TYPES"){
                        Ok(v) if v.starts_with("OK ")=>v,
                        _=>"ERR device type probe failed".into()
                    },
                    Err(_)=>"ERR camera unavailable".into()
                },
                Ok(_)=>"ERR preview not active".into(),
                Err(_)=>"ERR worker lock".into()
            }
        },
        "PREVIEW" if p.len()==2=>{
            let cfg=match system_config(rt,user){Ok(c)=>c,Err(_)=>return "ERR configure a camera first".into()};
            match pipe.lock(){Ok(mut x)=>match x.preview(rt,&cfg,user){Ok(v)=>v,Err(_)=>"ERR camera unavailable or preview stopped".into()},Err(_)=>"ERR worker lock".into()}
        },
        "ENROLL_CAPTURE" if p.len()==3=>{
            if protocol::normalize_label(p[2]).is_none(){return "ERR invalid label".into();}
            // 授权必须在采帧之前：取消授权则完全不碰摄像头、不写入模板。
            if let Err(e)=authorize_models(rt,uid,pid){return format!("ERR {e}");}
            let cfg=match system_config(rt,user){Ok(c)=>c,Err(_)=>return "ERR configure a camera first".into()};
            let face=match pipe.lock(){
                Ok(mut x)=>{
                    // Briefly yield camera exclusively to the five-frame capture.
                    // The GUI keeps its preview lease; after capture it resumes
                    // automatically, even when enrollment fails.
                    let existing=x.preview_owner.clone();
                    x.close_camera();
                    let result=x.enroll_capture(rt,&cfg);
                    x.close_camera();
                    if let Some(owner)=existing{x.start_preview(&owner);}
                    result
                },
                Err(_)=>Err("worker lock".into())
            };
            match face{Ok(e)=>{
                let mut st=match FaceStore::load(&path){Ok(s)=>s,Err(_)=>return "ERR existing template damaged".into()};
                if st.put(Face{label:p[2].into(),embedding:e}).is_err(){return "ERR face limit reached".into();}
                match st.save(&path){Ok(_)=>format!("OK enrolled:{}",p[2]),Err(_)=>"ERR store failed".into()}
            },Err(_)=>"ERR face capture failed; check camera/IR/model".into()}
        },
        "AUTH"|"TEST" if p.len()==2=>{
            let cfg=match system_config(rt,user){Ok(c)=>c,Err(_)=>return "IGNORE no-config".into()};
            let st=match FaceStore::load(&path){Ok(s)=>s,Err(_)=>return "IGNORE corrupt-template".into()};
            match pipe.lock(){Ok(mut x)=>{
                let existing=x.preview_owner.clone();
                x.close_camera();
                let v=x.authenticate(rt,&cfg,&st);
                x.close_camera();
                if let Some(owner)=existing{x.start_preview(&owner);}
                if auth {if v.starts_with("OK "){"OK".into()}else if v.starts_with("DENIED "){"DENIED".into()}else{"IGNORE".into()}}else{v}},Err(_)=>"IGNORE worker".into()}
        },
        _=>"ERR invalid command".into()
    }
}
fn serve(mut stream:UnixStream,rt:Arc<Runtime>,pipe:Arc<Mutex<Pipeline>>){
    let _=stream.set_read_timeout(Some(Duration::from_secs(5)));
    let _=stream.set_write_timeout(Some(Duration::from_secs(5)));
    let (uid,pid)=match peer::credentials(&stream){Ok(v)=>v,Err(_)=>return};
    let input=match protocol::read_request(&mut stream){Ok(i)=>i,Err(_)=>return};
    let response=handle(&rt,&pipe,uid,pid,&input);
    // No raw pixels, embeddings, usernames, paths in logs.
    let _=stream.write_all(response.as_bytes());let _=stream.write_all(b"\n");
}
fn run()->Result<(),Box<dyn std::error::Error>>{
    let dev=env::args().any(|a|a=="--dev");let uid=unsafe{geteuid()};
    if dev&&uid==0{return Err("--dev must not run as root".into());}
    if !dev&&uid!=0{return Err("system daemon must run as root; use --dev for tests".into());}
    let root=Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let (directory,script_dir,store_dir)=if dev{
        let base=PathBuf::from(env::var("XDG_RUNTIME_DIR")? ).join("faceauth-dev");
        let home=PathBuf::from(env::var("HOME")?);
        (base.clone(),root.join("scripts"),home.join(".local/share/faceauth-rebuild/models"))
    }else{
        (PathBuf::from("/run/faceauth-rebuild"),PathBuf::from("/usr/libexec/faceauth-rebuild/scripts"),PathBuf::from("/var/lib/faceauth-rebuild/models"))
    };
    fs::create_dir_all(&directory)?;
    fs::set_permissions(&directory,fs::Permissions::from_mode(if dev{0o700}else{0o755}))?;
    fs::create_dir_all(&store_dir)?;fs::set_permissions(&store_dir,fs::Permissions::from_mode(0o700))?;
    let socket=directory.join("daemon.sock");
    // A crashed system service can leave a stale root-owned Unix socket.
    // Never unlink a live socket, symlink, or a socket not owned by root.
    if fs::symlink_metadata(&socket).is_ok(){
        if dev{return Err(format!("development socket already exists: {}",socket.display()).into());}
        let meta=fs::symlink_metadata(&socket)?;
        if !meta.file_type().is_socket() || meta.uid()!=0 || UnixStream::connect(&socket).is_ok(){
            return Err(format!("unsafe or active socket at {}",socket.display()).into());
        }
        fs::remove_file(&socket)?;
    }
    let listener=UnixListener::bind(&socket)?;
    fs::set_permissions(&socket,fs::Permissions::from_mode(if dev{0o600}else{0o666}))?;
    let scene_dir=directory.clone();
    if dev {SceneManager::sandbox(&scene_dir).initialize_sandbox()?;}
    let rt=Arc::new(Runtime{dev,script_dir,store_dir,scene_dir});
    let pipe=Arc::new(Mutex::new(Pipeline::new()));
    if !dev {
        let warm_rt=rt.clone();
        let warm_pipe=pipe.clone();
        thread::spawn(move||{
            if let Ok(mut p)=warm_pipe.lock(){ let _=p.engine(&warm_rt); }
        });
    }
    let active=Arc::new(AtomicUsize::new(0));
    // Recover abandoned PREVIEW sessions (e.g., GUI crash) without needing
    // the GUI to send a final packet. Never interferes with an active request.
    let sweeper=pipe.clone();
    thread::spawn(move||loop{
        thread::sleep(Duration::from_secs(1));
        if let Ok(mut p)=sweeper.try_lock(){p.expire_preview();}
    });
    eprintln!("FaceAuth daemon v0.1.6 {:?} ready (PAM changes require admin polkit)",if dev{"dev"}else{"system"});
    for incoming in listener.incoming(){
        if let Ok(stream)=incoming{
            if active.fetch_add(1,Ordering::SeqCst)>=8{active.fetch_sub(1,Ordering::SeqCst);continue;}
            let (rt,pipe,active)=(rt.clone(),pipe.clone(),active.clone());
            thread::spawn(move||{serve(stream,rt,pipe);active.fetch_sub(1,Ordering::SeqCst);});
        }
    }
    Ok(())
}
fn main(){if let Err(err)=run(){eprintln!("faceauth-daemon: {err}");std::process::exit(1)}}
