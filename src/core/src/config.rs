use std::{fs, path::Path};

#[derive(Clone, Debug, PartialEq)]
pub struct Config {
    pub mode: String,
    pub rgb_device: String,
    pub ir_device: String,
    pub liveness: String,
    pub threshold: f32,
    pub auth_frames: usize,
    pub dark_ir_skip: bool,
    pub ssh_disabled: bool,
}
impl Default for Config {
    fn default() -> Self {
        Self {mode:"dual".into(), rgb_device:String::new(),ir_device:String::new(),
            liveness:"heuristic".into(),threshold:0.6,
            auth_frames:3, dark_ir_skip:true, ssh_disabled:true}
    }
}
impl Config {
    /// 认证取帧数，限制在 1..=5，避免异常配置突破 PAM 5 秒超时。
    pub fn effective_auth_frames(&self)->usize{self.auth_frames.clamp(1,5)}
}
impl Config {
    pub fn parse(text:&str) -> Result<Self,String> {
        let mut c=Self::default();
        for line in text.lines() {
            let line=line.split('#').next().unwrap_or("").trim();
            if line.is_empty(){continue;}
            let (key,val)=line.split_once('=').ok_or("invalid key=value")?;
            let val=val.trim();
            match key.trim(){
                "mode"=>c.mode=val.into(), "rgb_device"=>c.rgb_device=val.into(),
                "ir_device"=>c.ir_device=val.into(),
                "depth_device"=>{}, // backward-compatible parsing only; no capture implementation
                "liveness"=>c.liveness=val.into(),
                "threshold"=>c.threshold=val.parse().map_err(|_|"invalid threshold")?,
                "auth_frames"=>c.auth_frames=val.parse().map_err(|_|"invalid auth_frames")?,
                "dark_ir_skip"=>c.dark_ir_skip=val=="1"||val.eq_ignore_ascii_case("true"),
                "ssh_disabled"=>c.ssh_disabled=val!="0"&&!val.eq_ignore_ascii_case("false"),
                "infer_program"=>{}, // User may have legacy config; NEVER execute it as root.
                _=>return Err("unrecognized configuration key".into()),
            }
        }
        if !["rgb","ir","dual"].contains(&c.mode.as_str()) {return Err("invalid mode".into());}
        if !["none","heuristic"].contains(&c.liveness.as_str()) {return Err("invalid liveness".into());}
        if !c.threshold.is_finite() || !(0.0..=1.0).contains(&c.threshold) {return Err("invalid threshold".into());}
        Ok(c)
    }
    pub fn load(path:&Path)->Result<Self,String>{
        Self::parse(&fs::read_to_string(path).map_err(|e|e.to_string())?)
    }
    pub fn selected_device(&self)->&str{if self.mode=="ir"{&self.ir_device}else{&self.rgb_device}}
}
#[cfg(test)]mod tests{
    use super::*;
    #[test]fn legacy_device_key_ignored(){let c=Config::parse("mode=dual\ndepth_device=/dev/video99\nliveness=heuristic").unwrap();assert_eq!(c.mode,"dual");}
    #[test]fn obsolete_depth_liveness_rejected(){assert!(Config::parse("liveness=depth3d").is_err());}
    #[test]fn invalid(){assert!(Config::parse("threshold=nan").is_err());assert!(Config::parse("mode=raw").is_err());}
    #[test]fn ignores_unsafe_execution_path(){let c=Config::parse("infer_program=/tmp/attacker.py\nmode=rgb").unwrap();assert_eq!(c.mode,"rgb");}
    #[test]fn new_strategy_keys(){
        let c=Config::parse("auth_frames=5\ndark_ir_skip=0\nssh_disabled=0").unwrap();
        assert_eq!(c.effective_auth_frames(),5);
        assert!(!c.dark_ir_skip);
        assert!(!c.ssh_disabled);
        let d=Config::parse("auth_frames=99").unwrap();
        assert_eq!(d.effective_auth_frames(),5); // clamped
    }
}
