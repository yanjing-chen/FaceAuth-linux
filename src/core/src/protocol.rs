use std::io::{self, Read};

/// Fixed-size bounded read for unauthenticated clients. No unbounded read_line.
pub fn read_request<R:Read>(r:&mut R)->io::Result<String>{
    let mut buf=Vec::new();let mut one=[0u8;1];
    while buf.len()<8192 {
        if r.read(&mut one)?==0 {break;}
        if one[0]==b'\n' {break;}
        if one[0]==0 || one[0]==b'\r' {return Err(io::Error::new(io::ErrorKind::InvalidData,"invalid char"));}
        buf.push(one[0]);
    }
    if buf.len()>=8192 {return Err(io::Error::new(io::ErrorKind::InvalidData,"request too large"));}
    String::from_utf8(buf).map_err(|_|io::Error::new(io::ErrorKind::InvalidData,"not UTF8"))
}
pub fn normalize_label(text:&str)->Option<String>{
    if text.is_empty()||text.as_bytes().len()>64||text.contains(|c:char|c.is_whitespace()||c.is_control()||c==','||c=='/'||c=='\\') {return None;}
    Some(text.to_owned())
}
pub fn decode_embedding(s:&str)->Result<[f32;128],String>{
    if s.len()!=1024||!s.bytes().all(|x|x.is_ascii_hexdigit()){return Err("128 float32 require exactly 1024 hex digits".into());}
    let mut a=[0.0f32;128];
    for (i,v) in a.iter_mut().enumerate(){let p=&s[i*8..i*8+8];let mut b=[0u8;4];for n in 0..4{b[n]=u8::from_str_radix(&p[n*2..n*2+2],16).map_err(|e|e.to_string())?;}*v=f32::from_le_bytes(b);if !v.is_finite(){return Err("nonfinite vector".into());}}
    let norm=a.iter().map(|x|(*x as f64)*(*x as f64)).sum::<f64>().sqrt();
    if !(0.01..=1e8).contains(&norm){return Err("embedding norm invalid".into());}
    for v in &mut a {*v=(*v as f64/norm) as f32;}Ok(a)
}
pub fn encode_embedding(e:&[f32;128])->String{let mut s=String::with_capacity(1024);for f in e{s.push_str(&format!("{}",f.to_le_bytes().iter().map(|b|format!("{b:02x}")).collect::<String>()));}s}
pub fn similarity(a:&[f32;128],b:&[f32;128])->f32{a.iter().zip(b).map(|(x,y)|x*y).sum()}
#[cfg(test)]mod tests{
    use super::*;
    #[test]fn hex(){let mut v=[0f32;128];v[0]=1.;let x=encode_embedding(&v);assert_eq!(x.len(),1024);assert_eq!(decode_embedding(&x).unwrap()[0],1.);assert!(decode_embedding("00").is_err());}
    #[test]fn label(){assert!(normalize_label("a b").is_none());assert!(normalize_label("测试-2").is_some());}
    #[test]fn bounded(){assert!(read_request(&mut &b"PING\n"[..]).unwrap()=="PING");assert!(read_request(&mut &vec![b'a';9000][..]).is_err());}
}
