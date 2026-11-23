//! FA02: root-owned binary face store, bounded decode, CRC32 for corruption detection.
//! Checksum detects accident, not malicious modification: filesystem permissions are the trust boundary.
use std::{fs::{self,OpenOptions},io::{self,Write},os::unix::fs::{PermissionsExt,OpenOptionsExt},path::Path};
use crate::protocol::{normalize_label,similarity};
#[derive(Clone,Debug)]pub struct Face{pub label:String,pub embedding:[f32;128]}
#[derive(Clone,Default)]pub struct FaceStore{pub faces:Vec<Face>}
fn crc32(data:&[u8])->u32{let mut crc=0xffffffff_u32;for byte in data{crc^=*byte as u32;for _ in 0..8{crc=(crc>>1)^if crc&1==1{0xedb88320}else{0};}}!crc}
impl FaceStore {
    pub fn put(&mut self,face:Face)->Result<(),String>{
        if normalize_label(&face.label).is_none(){return Err("bad label".into());}
        if face.embedding.iter().any(|v|!v.is_finite()){return Err("bad embedding".into());}
        if let Some(i)=self.faces.iter().position(|f|f.label==face.label){self.faces[i]=face;}
        else if self.faces.len()<16 {self.faces.push(face);}else{return Err("too many faces".into());}Ok(())
    }
    pub fn remove(&mut self,label:&str)->bool{let old=self.faces.len();self.faces.retain(|f|f.label!=label);self.faces.len()!=old}
    pub fn best_match(&self,embedding:&[f32;128])->Option<(&str,f32)>{self.faces.iter().map(|f|(f.label.as_str(),similarity(&f.embedding,embedding))).max_by(|a,b|a.1.total_cmp(&b.1))}
    pub fn serialize(&self)->Vec<u8>{
        let mut v=Vec::new();v.extend_from_slice(b"FA02");v.push(self.faces.len() as u8);
        for f in &self.faces{let l=f.label.as_bytes();v.push(l.len() as u8);v.extend_from_slice(l);for x in f.embedding{v.extend_from_slice(&x.to_le_bytes());}}
        let crc=crc32(&v);v.extend_from_slice(&crc.to_le_bytes());v
    }
    pub fn parse(raw:&[u8])->Result<Self,String>{
        if raw.len()<9||raw.len()>10000{return Err("bad template length".into());}
        if &raw[..4]!=b"FA02"{return Err("unknown template format; migrate offline".into());}
        let tail=raw.len()-4;
        if crc32(&raw[..tail])!=u32::from_le_bytes(raw[tail..].try_into().unwrap()){return Err("checksum mismatch".into());}
        let count=raw[4] as usize;if count>16{return Err("too many faces".into());}
        let mut pos=5;let mut st=Self::default();
        for _ in 0..count{
            if pos>=tail{return Err("truncated label".into());}let n=raw[pos] as usize;pos+=1;
            if pos+n+512>tail{return Err("truncated face".into());}
            let label=std::str::from_utf8(&raw[pos..pos+n]).map_err(|_|"bad UTF8")?.to_string();pos+=n;
            let mut embedding=[0f32;128];for v in &mut embedding{*v=f32::from_le_bytes(raw[pos..pos+4].try_into().unwrap());pos+=4;}
            st.put(Face{label,embedding})?;
        }
        if pos!=tail || st.faces.len()!=count{return Err("template trailing or duplicate data".into());}Ok(st)
    }
    pub fn load(path:&Path)->Result<Self,String>{match fs::read(path){Ok(raw)=>Self::parse(&raw),Err(ref e)if e.kind()==io::ErrorKind::NotFound=>Ok(Self::default()),Err(e)=>Err(e.to_string())}}
    pub fn save(&self,path:&Path)->io::Result<()>{
        let parent=path.parent().ok_or_else(||io::Error::other("no parent"))?;
        fs::create_dir_all(parent)?;fs::set_permissions(parent,fs::Permissions::from_mode(0o700))?;
        let tmp=path.with_extension(format!("new-{}",std::process::id()));
        let result=(||->io::Result<()>{
            let mut f=OpenOptions::new().write(true).create_new(true).mode(0o600).open(&tmp)?;
            f.write_all(&self.serialize())?;f.sync_all()?;fs::rename(&tmp,path)?;Ok(())})();
        if result.is_err(){let _=fs::remove_file(tmp);}result
    }
}
#[cfg(test)]mod tests{use super::*;
 #[test]fn roundtrip(){let mut s=FaceStore::default();let mut a=[0f32;128];a[0]=1.;s.put(Face{label:"人脸一".into(),embedding:a}).unwrap();let v=s.serialize();let s2=FaceStore::parse(&v).unwrap();assert_eq!(s2.best_match(&a).unwrap().0,"人脸一");let mut bad=v.clone();bad[5]^=1;assert!(FaceStore::parse(&bad).is_err());}
}
