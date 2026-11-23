//! SO_PEERCRED proof and numeric uid-to-name via local /etc/passwd.
use std::{fs,io,mem,os::fd::AsRawFd,os::raw::{c_int,c_void},os::unix::net::UnixStream};
const SOL_SOCKET:c_int=1;const SO_PEERCRED:c_int=17;
#[repr(C)]#[derive(Default,Copy,Clone)]struct UCred{pid:i32,uid:u32,gid:u32}
unsafe extern "C" {fn getsockopt(fd:c_int,level:c_int,opt:c_int,val:*mut c_void,len:*mut u32)->c_int;}
pub fn credentials(stream:&UnixStream)->io::Result<(u32,i32)>{let mut cred=UCred::default();let mut size=mem::size_of::<UCred>() as u32;
 let ret=unsafe{getsockopt(stream.as_raw_fd(),SOL_SOCKET,SO_PEERCRED,&mut cred as *mut _ as *mut c_void,&mut size)};
 if ret!=0||size!=mem::size_of::<UCred>() as u32 {return Err(io::Error::last_os_error());}Ok((cred.uid,cred.pid))}
pub fn user_uid(user:&str)->Option<u32>{if user.is_empty()||user.bytes().any(|b|!(b.is_ascii_alphanumeric()||b==b'_'||b==b'-'||b==b'.')){return None;}
 let content=fs::read_to_string("/etc/passwd").ok()?;
 content.lines().filter_map(|line|{let parts:Vec<_>=line.split(':').collect();if parts.len()<7||parts[0]!=user{return None;}parts[2].parse().ok()}).next()
}
pub fn home_dir(user:&str)->Option<String>{let text=fs::read_to_string("/etc/passwd").ok()?;text.lines().find_map(|line|{let x:Vec<_>=line.split(':').collect();if x.len()<7||x[0]!=user{return None;}Some(x[5].to_owned())})}
#[cfg(test)]mod tests{use super::*;#[test]fn refuses_path(){assert!(user_uid("root/../../").is_none());}}
