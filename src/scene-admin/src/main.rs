//! Root-only, offline recovery. Works even if FaceAuth daemon is down.
use faceauth_core::scene::{SceneManager,SERVICES,allowed_service};
unsafe extern "C"{fn geteuid()->u32;}
fn main(){
    if unsafe{geteuid()}!=0{eprintln!("faceauth-scenes: run as root for offline recovery");std::process::exit(1);}
    let args:Vec<_>=std::env::args().skip(1).collect();
    let manager=SceneManager::production();
    if args.len()==1 && args[0]=="list" {
        for service in SERVICES{println!("{service}: {:?}",manager.status(service));}return;
    }
    if args.len()!=2||!matches!(args[0].as_str(),"on"|"off")||allowed_service(&args[1]).is_none(){
        eprintln!("Usage: sudo faceauth-scenes list | on|off sudo|gdm|su|login|polkit");
        std::process::exit(2);
    }
    match manager.set(&args[1],args[0]=="on"){
        Ok(s)=>println!("{s}"),Err(e)=>{eprintln!("ERROR: {e}");std::process::exit(1)}
    }
}
