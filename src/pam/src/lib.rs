//! PAM boundary: no Python, threads, shell, graphics, models, or external crate.
//! All failures return PAM_IGNORE and leave system's existing password flow intact.
use std::{ffi::{c_char,c_int,c_void,CStr},io::{Read,Write},os::unix::net::UnixStream,time::Duration};
const PAM_SUCCESS:c_int=0;
const PAM_IGNORE:c_int=25;
#[link(name="pam")]
unsafe extern "C" {
    fn pam_get_user(pamh:*mut c_void,user:*mut *const c_char,prompt:*const c_char)->c_int;
    fn pam_getenv(pamh:*mut c_void,name:*const c_char)->*const c_char;
}
/// SSH 远程会话检测：从 PAM 环境读 SSH_CONNECTION / SSH_TTY，
/// 命中即回退密码，不在远程会话中尝试刷脸（远程通常无本地摄像头）。
fn pam_env(pamh:*mut c_void,name:&str)->Option<String>{
    let key=format!("{name}\0");
    let p=unsafe{pam_getenv(pamh,key.as_ptr() as *const c_char)};
    if p.is_null(){return None;}
    unsafe{CStr::from_ptr(p)}.to_str().ok().map(|s|s.to_owned())
}
fn in_ssh_session(pamh:*mut c_void)->bool{
    pam_env(pamh,"SSH_CONNECTION").is_some()||pam_env(pamh,"SSH_TTY").is_some()
}
/// 用户是否允许在 SSH 会话中刷脸。默认禁止（安全默认）；
/// 仅当 ~/.config/faceauth-linux/config 明确写 ssh_disabled=0/false 时才允许。
fn ssh_face_allowed(pamh:*mut c_void,user:&str)->bool{
    let home=match pam_env(pamh,"HOME"){Some(h) if !h.is_empty()=>h,
        _=>return false}; // 读不到 home -> 保持安全默认（禁止）
    if !user.bytes().all(|c|c.is_ascii_alphanumeric()||c==b'_'||c==b'-'||c==b'.'){return false;}
    let path=format!("{home}/.config/faceauth-linux/config");
    let text=match std::fs::read_to_string(&path){Ok(t)=>t,Err(_)=>return false};
    for line in text.lines(){
        let line=line.split('#').next().unwrap_or("").trim();
        if let Some(eq)=line.find('='){
            let k=line[..eq].trim();
            let v=line[eq+1..].trim();
            if k=="ssh_disabled"{return v=="0"||v.eq_ignore_ascii_case("false");}
        }
    }
    false // 未配置 -> 禁止 SSH 刷脸
}
fn authenticate(pamh:*mut c_void)->Result<bool,()> {
    if pamh.is_null(){return Err(());}
    let mut user:*const c_char=std::ptr::null();
    if unsafe{pam_get_user(pamh,&mut user,std::ptr::null())}!=PAM_SUCCESS||user.is_null(){return Err(());}
    let u=unsafe{CStr::from_ptr(user)}.to_str().map_err(|_|())?;
    if u.is_empty()||u.len()>128||!u.bytes().all(|c|c.is_ascii_alphanumeric()||c==b'_'||c==b'-'||c==b'.'){return Err(());}
    // SSH 远程会话：默认回退密码，不触发摄像头（仅用户显式关闭时才继续刷脸）。
    if in_ssh_session(pamh) && !ssh_face_allowed(pamh,u){return Err(());}
    let mut s=UnixStream::connect("/run/faceauth-linux/daemon.sock").map_err(|_|())?;
    s.set_read_timeout(Some(Duration::from_secs(12))).map_err(|_|())?;
    s.set_write_timeout(Some(Duration::from_secs(5))).map_err(|_|())?;
    s.write_all(format!("AUTH {u}\n").as_bytes()).map_err(|_|())?;
    let mut buf=[0u8;64];let n=s.read(&mut buf).map_err(|_|())?;
    Ok(n>=3 && (buf[..n].starts_with(b"OK\n") || &buf[..n]==b"OK"))
}
#[no_mangle]pub extern "C" fn pam_sm_authenticate(pamh:*mut c_void,_flags:c_int,_argc:c_int,_argv:*const *const c_char)->c_int{
    match authenticate(pamh){Ok(true)=>PAM_SUCCESS,_=>PAM_IGNORE}
}
#[no_mangle]pub extern "C" fn pam_sm_setcred(_p:*mut c_void,_f:c_int,_c:c_int,_a:*const *const c_char)->c_int{PAM_IGNORE}
#[no_mangle]pub extern "C" fn pam_sm_acct_mgmt(_p:*mut c_void,_f:c_int,_c:c_int,_a:*const *const c_char)->c_int{PAM_IGNORE}
