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
    /// IR 暗帧判定阈值（8 桶直方图最低桶占比 %），超过则跳过该帧。
    pub dark_threshold: f32,
    /// 认证取帧总时间预算（秒），与帧数上限双保险。
    pub auth_timeout: f32,
    /// 启发式活体评分通过门槛（0~1），越高越防伪、越低越易通过。
    pub liveness_strictness: f32,
}
impl Default for Config {
    fn default() -> Self {
        Self {mode:"dual".into(), rgb_device:String::new(),ir_device:String::new(),
            liveness:"heuristic".into(),threshold:0.6,
            auth_frames:3, dark_ir_skip:true, ssh_disabled:true,
            dark_threshold:60.0, auth_timeout:4.0, liveness_strictness:0.5}
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
                "dark_threshold"=>c.dark_threshold=val.parse().map_err(|_|"invalid dark_threshold")?,
                "auth_timeout"=>c.auth_timeout=val.parse().map_err(|_|"invalid auth_timeout")?,
                "liveness_strictness"=>c.liveness_strictness=val.parse().map_err(|_|"invalid liveness_strictness")?,
                "infer_program"=>{}, // User may have legacy config; NEVER execute it as root.
                _=>return Err("unrecognized configuration key".into()),
            }
        }
        if !["rgb","ir","dual"].contains(&c.mode.as_str()) {return Err("invalid mode".into());}
        if !["none","heuristic"].contains(&c.liveness.as_str()) {return Err("invalid liveness".into());}
        if !c.threshold.is_finite() || !(0.0..=1.0).contains(&c.threshold) {return Err("invalid threshold".into());}
        // 新调优项：clamp 而非报错，避免老配置/手改值导致认证整体不可用。
        if !c.dark_threshold.is_finite(){c.dark_threshold=60.0;}
        c.dark_threshold=c.dark_threshold.clamp(20.0,95.0);
        if !c.auth_timeout.is_finite(){c.auth_timeout=4.0;}
        c.auth_timeout=c.auth_timeout.clamp(1.5,4.5); // PAM 5s 内留余量
        if !c.liveness_strictness.is_finite(){c.liveness_strictness=0.5;}
        c.liveness_strictness=c.liveness_strictness.clamp(0.0,1.0);
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
    #[test]fn tunable_defaults_and_clamp(){
        let c=Config::parse("mode=dual\nthreshold=0.6").unwrap();
        assert_eq!(c.dark_threshold,60.0);
        assert_eq!(c.auth_timeout,4.0);
        assert_eq!(c.liveness_strictness,0.5);
        let d=Config::parse("dark_threshold=88\nauth_timeout=2.5\nliveness_strictness=0.8").unwrap();
        assert_eq!(d.dark_threshold,88.0);
        assert_eq!(d.auth_timeout,2.5);
        assert_eq!(d.liveness_strictness,0.8);
        let e=Config::parse("dark_threshold=999\nauth_timeout=0\nliveness_strictness=2").unwrap();
        assert_eq!(e.dark_threshold,95.0);
        assert_eq!(e.auth_timeout,1.5);
        assert_eq!(e.liveness_strictness,1.0);
    }
    #[test]fn new_strategy_keys(){
        let c=Config::parse("auth_frames=5\ndark_ir_skip=0\nssh_disabled=0").unwrap();
        assert_eq!(c.effective_auth_frames(),5);
        assert!(!c.dark_ir_skip);
        assert!(!c.ssh_disabled);
        let d=Config::parse("auth_frames=99").unwrap();
        assert_eq!(d.effective_auth_frames(),5); // clamped
    }
}
