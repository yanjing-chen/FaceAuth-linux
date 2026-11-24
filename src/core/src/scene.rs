//! PAM-scene transaction manager. No arbitrary service names or custom paths.
//! Modifications are only performed by an authorized system daemon or in an
//! explicitly isolated development directory. Never touches common-auth/other.
use std::{fs, io::{self,Write},os::unix::fs::{MetadataExt,OpenOptionsExt,PermissionsExt},path::{Path,PathBuf},time::{SystemTime,UNIX_EPOCH}};

pub const SERVICES:[&str;5]=["sudo","gdm","su","login","polkit"];
pub const PAM_MODULE:&str="/usr/lib/faceauth-linux/pam_faceauth_linux.so";
const BEGIN:&str="# FaceAuth-Rebuild managed BEGIN";
const VENDOR_BEGIN:&str="# FaceAuth-Rebuild managed BEGIN vendor-override";
const END:&str="# FaceAuth-Rebuild managed END";
const PAM_AUTH:&str="auth    [success=done default=ignore]    /usr/lib/faceauth-linux/pam_faceauth_linux.so";

pub fn allowed_service(name:&str)->Option<&'static str>{match name{
    "sudo"=>Some("sudo"),"gdm"=>Some("gdm-password"),
    "su"=>Some("su"),"login"=>Some("login"),"polkit"=>Some("polkit-1"),_=>None}}

pub struct SceneManager { pam_dir:PathBuf, vendor_dir:PathBuf,backup_dir:PathBuf, dev:bool }
impl SceneManager {
    pub fn production()->Self{Self{pam_dir:"/etc/pam.d".into(),vendor_dir:"/usr/lib/pam.d".into(),backup_dir:"/var/lib/faceauth-linux/backups".into(),dev:false}}
    pub fn sandbox(base:&Path)->Self{Self{pam_dir:base.join("pam.d"),vendor_dir:base.join("vendor-pam.d"),backup_dir:base.join("backups"),dev:true}}
    pub fn initialize_sandbox(&self)->Result<(),String>{
        if !self.dev{return Err("sandbox initialization forbidden in system mode".into());}
        fs::create_dir_all(&self.pam_dir).map_err(|e|e.to_string())?;
        fs::create_dir_all(&self.vendor_dir).map_err(|e|e.to_string())?;
        for s in SERVICES {
            let target=if s=="polkit"{self.vendor_dir.join("polkit-1")}else{self.pam_dir.join(allowed_service(s).unwrap())};
            if !target.exists(){fs::write(&target,"# SANDBOX ONLY; not a system PAM profile\n@include common-auth\n@include common-account\n").map_err(|e|e.to_string())?;}
        }
        Ok(())
    }
    fn checked_file(&self,path:&Path)->Result<Option<(String,u32)>,String>{
        let meta=match fs::symlink_metadata(path){Ok(m)=>m,Err(e)if e.kind()==io::ErrorKind::NotFound=>return Ok(None),Err(e)=>return Err(e.to_string())};
        if !meta.file_type().is_file()||(!self.dev && (meta.uid()!=0 || meta.mode()&0o022!=0)){
            return Err(format!("unsafe PAM file: {}",path.display()));
        }
        let text=fs::read_to_string(path).map_err(|e|e.to_string())?;
        if text.len()>128*1024{return Err("oversized PAM profile".into());}
        Ok(Some((text,meta.mode()&0o777)))
    }
    fn paths(&self,service:&str)->Result<(PathBuf,PathBuf),String>{
        let name=allowed_service(service).ok_or("not an allowed PAM service")?;
        Ok((self.pam_dir.join(name),self.vendor_dir.join(name)))
    }
    pub fn status(&self,service:&str)->Result<bool,String>{
        let (path,_)=self.paths(service)?;
        match self.checked_file(&path)? {
            None=>Ok(false),
            Some((body,_))=>{
                if body.contains(BEGIN) {Self::validate_managed(&body).map(|_|true)}
                else if body.contains(PAM_MODULE){Err("foreign PAM entry conflicts with FaceAuth-Rebuild".into())}
                else {Ok(false)}
            }
        }
    }
    fn validate_managed(text:&str)->Result<bool,String>{
        let begins=text.lines().filter(|line|*line==BEGIN || *line==VENDOR_BEGIN).count();
        let ends=text.lines().filter(|line|*line==END).count();
        let auths=text.lines().filter(|line|*line==PAM_AUTH).count();
        if begins!=1||ends!=1||auths!=1 {return Err("FaceAuth PAM markers damaged; refusing automatic changes".into());}
        let vendor=text.lines().any(|line|line==VENDOR_BEGIN);
        let block=Self::block(vendor);
        if text.matches(block.as_str()).count()!=1{return Err("PAM managed block modified; refusing automatic changes".into());}
        Ok(vendor)
    }
    fn block(vendor:bool)->String{format!("{}\n{}\n{}\n",if vendor{VENDOR_BEGIN}else{BEGIN},PAM_AUTH,END)}
    fn insert(original:&str,vendor:bool)->Result<String,String>{
        if original.contains(BEGIN)||original.contains(PAM_MODULE)
            ||original.lines().any(|line|line.trim_start().starts_with("auth")
                && line.contains("pam_faceauth.so")) {
            return Err("another FaceAuth PAM module or managed entry is already present; refusing duplicate".into());
        }
        let mut out=String::with_capacity(original.len()+300);
        let mut done=false;
        for line in original.split_inclusive('\n') {
            if !done && line.trim()=="@include common-auth" {
                out.push_str(&Self::block(vendor));done=true;
            }
            out.push_str(line);
        }
        if !done{return Err("PAM profile lacks common-auth fallback; refusing to mount".into());}
        Ok(out)
    }
    fn backup(&self,service:&str,content:&str)->Result<(),String>{
        fs::create_dir_all(&self.backup_dir).map_err(|e|e.to_string())?;
        if !self.dev{
            let meta=fs::symlink_metadata(&self.backup_dir).map_err(|e|e.to_string())?;
            if !meta.file_type().is_dir() || meta.uid()!=0 || meta.mode()&0o077!=0 {
                return Err("unsafe backup directory ownership or permissions".into());
            }
        }
        fs::set_permissions(&self.backup_dir,fs::Permissions::from_mode(0o700)).map_err(|e|e.to_string())?;
        let now=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|e|e.to_string())?.as_nanos();
        let backup=self.backup_dir.join(format!("{service}.{now}.bak"));
        let mut file=fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(backup).map_err(|e|e.to_string())?;
        file.write_all(content.as_bytes()).map_err(|e|e.to_string())?;
        file.sync_all().map_err(|e|e.to_string())?;
        Ok(())
    }
    fn atomic_replace(path:&Path,body:&str,mode:u32)->Result<(),String>{
        let parent=path.parent().ok_or("missing PAM directory")?;
        let now=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|e|e.to_string())?.as_nanos();
        let temp=parent.join(format!(".faceauth-linux-{}-{}",std::process::id(),now));
        let operation=(||->Result<(),String>{
            let mut f=fs::OpenOptions::new().create_new(true).write(true).mode(0o600).open(&temp).map_err(|e|e.to_string())?;
            f.write_all(body.as_bytes()).map_err(|e|e.to_string())?;
            f.sync_all().map_err(|e|e.to_string())?;
            fs::set_permissions(&temp,fs::Permissions::from_mode(mode)).map_err(|e|e.to_string())?;
            fs::rename(&temp,path).map_err(|e|e.to_string())?;
            fs::File::open(parent).and_then(|dir|dir.sync_all()).map_err(|e|e.to_string())?;
            Ok(())
        })();
        if operation.is_err(){let _=fs::remove_file(&temp);}operation
    }
    pub fn set(&self,service:&str,enabled:bool)->Result<String,String>{
        let (path,vendor_path)=self.paths(service)?;
        if enabled && !self.dev && !Path::new(PAM_MODULE).is_file(){return Err("FaceAuth PAM module not installed; use install-system.sh first".into());}
        if !self.dev {
            let meta=fs::symlink_metadata(&self.pam_dir).map_err(|e|e.to_string())?;
            if !meta.file_type().is_dir()||meta.uid()!=0||meta.mode()&0o022!=0{return Err("unsafe PAM directory".into());}
        }
        let (existing,origin_vendor) = match self.checked_file(&path)? {
            Some((body,mode))=>(Some((body,mode)),false),
            None if !enabled=>return Ok(format!("OK {service}:already-off")),
            None if service=="polkit"=>{
                let (body,mode)=self.checked_file(&vendor_path)?.ok_or("vendor polkit-1 PAM stack absent; refuse blind override")?;
                (Some((body,mode)),true)
            },
            None=>return Err("service PAM profile missing; refusing to create a new one".into())
        };
        let (body,mode)=existing.unwrap();
        if enabled {
            if !origin_vendor && self.status(service)?{return Ok(format!("OK {service}:already-on"));}
            let next=Self::insert(&body,origin_vendor)?;
            self.backup(service,&body)?;
            Self::atomic_replace(&path,&next,mode)?;
            Ok(format!("OK {service}:on"))
        }else{
            if origin_vendor{return Ok(format!("OK {service}:already-off"));}
            if !body.contains(BEGIN) {
                if body.contains(PAM_MODULE){return Err("foreign FaceAuth entry; cannot disable".into());}
                return Ok(format!("OK {service}:already-off"));
            }
            let copied_vendor=Self::validate_managed(&body)?;
            let managed_block=Self::block(copied_vendor);
            let stripped=body.replacen(managed_block.as_str(),"",1);
            self.backup(service,&body)?;
            if copied_vendor {
                let current_vendor=self.checked_file(&vendor_path)?.ok_or("polkit vendor file missing; manual recovery needed")?.0;
                if stripped!=current_vendor {return Err("polkit override changed since creation; preserving local override".into());}
                fs::remove_file(&path).map_err(|e|e.to_string())?;
            }else{
                Self::atomic_replace(&path,&stripped,mode)?;
            }
            Ok(format!("OK {service}:off"))
        }
    }
}
#[cfg(test)] mod tests {
    use super::*;
    fn sandbox()->(SceneManager,PathBuf){
        let p=std::env::temp_dir().join(format!("faceauth-scenes-{}-{}",std::process::id(),SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()));
        let mgr=SceneManager::sandbox(&p);mgr.initialize_sandbox().unwrap();(mgr,p)
    }
    #[test]fn allowlist(){assert!(allowed_service("common-auth").is_none());assert!(allowed_service("../sudo").is_none());assert_eq!(SERVICES.len(),5);}
    #[test]fn mount_unmount_does_not_break_password(){let (mgr,path)=sandbox();
        for name in SERVICES {assert!(!mgr.status(name).unwrap());mgr.set(name,true).unwrap();assert!(mgr.status(name).unwrap());
            assert!(mgr.set(name,true).unwrap().contains("already-on"));let (pam,_) = mgr.paths(name).unwrap();let profile=fs::read_to_string(&pam).unwrap();
            assert!(profile.contains("[success=done default=ignore]"));assert!(profile.contains("@include common-auth"));
            mgr.set(name,false).unwrap();assert!(!mgr.status(name).unwrap());}
        assert!(!path.join("pam.d/common-auth").exists());let _=fs::remove_dir_all(path);
    }
    #[test]fn refuse_foreign_edits(){let (mgr,path)=sandbox();let target=path.join("pam.d/sudo");
        fs::write(&target,"auth required pam_custom.so\n").unwrap();assert!(mgr.set("sudo",true).is_err());
        fs::write(&target,"@include common-auth\n").unwrap();mgr.set("sudo",true).unwrap();
        let modified=fs::read_to_string(&target).unwrap().replace("default=ignore","default=bad");fs::write(&target,modified).unwrap();
        assert!(mgr.set("sudo",false).is_err());let _=fs::remove_dir_all(path);
    }
}
