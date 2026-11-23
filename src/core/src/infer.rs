//! Manage a trusted, persistent, line-protocol child process. No shell.
use std::{io::{self,BufRead,BufReader,Write},os::{fd::AsRawFd,raw::c_int},process::{Child,ChildStdin,ChildStdout,Command,Stdio}};
#[repr(C)]struct PollFd{fd:c_int,events:i16,revents:i16}
unsafe extern "C" {fn poll(fds:*mut PollFd,nfds:usize,timeout:c_int)->c_int;}

pub struct Worker{child:Child,input:ChildStdin,output:BufReader<ChildStdout>}
impl Worker{
    pub fn launch(script:&str)->io::Result<Self>{
        let mut child=Command::new("/usr/bin/python3").arg("-I").arg("-u").arg(script)
           .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit())
           .env_remove("PYTHONPATH").env_remove("PYTHONHOME").spawn()?;
        let input=child.stdin.take().ok_or_else(||io::Error::other("no child stdin"))?;
        let output=BufReader::new(child.stdout.take().ok_or_else(||io::Error::other("no child stdout"))?);
        Ok(Self{child,input,output})
    }
    pub fn query(&mut self,line:&str)->io::Result<String>{
        if line.len()>2_000_000||line.contains('\n'){return Err(io::Error::other("invalid request"));}
        self.input.write_all(line.as_bytes())?;self.input.write_all(b"\n")?;self.input.flush()?;
        let mut fd=PollFd{fd:self.output.get_ref().as_raw_fd(),events:1,revents:0};
        // Python inference initialization can be slower; subsequent camera/ML
        // requests are bounded so a dead worker does not hold the daemon lock.
        let timeout_ms=if line=="PING"{20000}else{3500};
        let ready=unsafe{poll(&mut fd,1,timeout_ms)};
        if ready<=0 || fd.revents&1==0{return Err(io::Error::new(io::ErrorKind::TimedOut,"worker response timeout"));}
        let mut output=String::new();self.output.read_line(&mut output)?;
        if output.len()>4_000_000{return Err(io::Error::other("reply too long"));}
        if output.is_empty(){return Err(io::Error::new(io::ErrorKind::UnexpectedEof,"worker closed"));}
        Ok(output.trim_end().into())
    }
    pub fn ping(&mut self)->io::Result<()> {if self.query("PING")?=="PONG"{Ok(())}else{Err(io::Error::other("bad worker handshake"))}}
}
impl Drop for Worker{fn drop(&mut self){let _=self.child.kill();let _=self.child.wait();}}
