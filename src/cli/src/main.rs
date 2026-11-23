use std::{env,io::{Read,Write},os::unix::net::UnixStream,time::Duration};
fn main(){
    let args:Vec<String>=env::args().skip(1).collect();
    if args.is_empty(){eprintln!("Usage: faceauth-cli [--dev] PING | TEST user | LIST user | DELETE_FACE user label | ...");std::process::exit(2);}
    let dev=args.first().map(|x|x=="--dev").unwrap_or(false);
    let cmd=if dev{&args[1..]}else{&args[..]};
    if cmd.is_empty()||cmd.iter().any(|x|x.contains(char::is_whitespace)){eprintln!("invalid command");std::process::exit(2);}
    let socket=if dev{format!("{}/faceauth-dev/daemon.sock",env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_|format!("/run/user/{}",env::var("UID").unwrap_or_else(|_|"1000".into()))))}else{"/run/faceauth-rebuild/daemon.sock".into()};
    let mut s=UnixStream::connect(socket).expect("cannot connect to FaceAuth daemon");
    s.set_read_timeout(Some(Duration::from_secs(30))).ok();s.set_write_timeout(Some(Duration::from_secs(5))).ok();
    s.write_all(format!("{}\n",cmd.join(" ")).as_bytes()).expect("write failed");
    let mut reply=String::new();s.read_to_string(&mut reply).expect("read failed");
    println!("{}",reply.trim());
}
