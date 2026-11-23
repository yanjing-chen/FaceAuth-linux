//! PAM boundary: no Python, threads, shell, graphics, models, or external crate.
//! All failures return PAM_IGNORE and leave system's existing password flow intact.
use std::{ffi::{c_char,c_int,c_void,CStr},io::{Read,Write},os::unix::net::UnixStream,time::Duration};
const PAM_SUCCESS:c_int=0;
const PAM_IGNORE:c_int=25;
#[link(name="pam")]
unsafe extern "C" {fn pam_get_user(pamh:*mut c_void,user:*mut *const c_char,prompt:*const c_char)->c_int;}
fn authenticate(pamh:*mut c_void)->Result<bool,()> {
    if pamh.is_null(){return Err(());}
    let mut user:*const c_char=std::ptr::null();
    if unsafe{pam_get_user(pamh,&mut user,std::ptr::null())}!=PAM_SUCCESS||user.is_null(){return Err(());}
    let u=unsafe{CStr::from_ptr(user)}.to_str().map_err(|_|())?;
    if u.is_empty()||u.len()>128||!u.bytes().all(|c|c.is_ascii_alphanumeric()||c==b'_'||c==b'-'||c==b'.'){return Err(());}
    let mut s=UnixStream::connect("/run/faceauth-rebuild/daemon.sock").map_err(|_|())?;
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
