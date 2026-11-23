use std::{fs, path::Path};

#[derive(Clone, Debug, PartialEq)]
pub struct Config {
    pub mode: String,
    pub rgb_device: String,
    pub ir_device: String,
    pub liveness: String,
    pub threshold: f32,
}
impl Default for Config {
    fn default() -> Self {
        Self {mode:"dual".into(), rgb_device:String::new(),ir_device:String::new(),
            liveness:"heuristic".into(),threshold:0.6}
    }
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
}
