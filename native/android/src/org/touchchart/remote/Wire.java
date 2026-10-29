package org.touchchart.remote;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;

/** Shared real transport, usable by Android and the JVM protocol acceptance test. */
public final class Wire {
    public static String normalizeAddress(String address) {
        String value=address.trim();
        if (!value.startsWith("http://")) value="http://"+value;
        URI uri=URI.create(value);
        if (!"http".equals(uri.getScheme()) || uri.getHost()==null || uri.getUserInfo()!=null || uri.getQuery()!=null || uri.getFragment()!=null)
            throw new IllegalArgumentException("请输入电脑显示的 IP:端口，例如 192.168.1.5:8790");
        int port=uri.getPort()<0?8790:uri.getPort();
        if(port<1 || port>65535)throw new IllegalArgumentException("端口无效");
        return "http://"+uri.getHost()+":"+port;
    }
    public static String request(String base,String route,String json) throws IOException {
        HttpURLConnection conn=(HttpURLConnection)new URL(base+route).openConnection();
        conn.setConnectTimeout(2500);conn.setReadTimeout(2500);conn.setUseCaches(false);
        try {
            if(json!=null){
                byte[] bytes=json.getBytes(StandardCharsets.UTF_8);
                conn.setRequestMethod("POST");conn.setDoOutput(true);conn.setFixedLengthStreamingMode(bytes.length);
                conn.setRequestProperty("Content-Type","application/json; charset=utf-8");
                try(OutputStream out=conn.getOutputStream()){out.write(bytes);}
            }
            int status=conn.getResponseCode();
            InputStream stream=status>=400?conn.getErrorStream():conn.getInputStream();
            if(stream==null)throw new IOException("HTTP "+status);
            ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] buffer=new byte[4096];int size;
            try(InputStream in=stream){while((size=in.read(buffer))!=-1){out.write(buffer,0,size);if(out.size()>4*1024*1024)throw new IOException("响应过大");}}
            String text=new String(out.toByteArray(),StandardCharsets.UTF_8);
            if(status>=400)throw new IOException("HTTP "+status+" "+text);
            return text;
        } finally {conn.disconnect();}
    }
    public static double synchronizedOffset(double before,double after,double serverTime){return serverTime-(before+after)/2.0;}
    public static float normalized(float value,float extent){return Math.min(1f,Math.max(0f,value/Math.max(1f,extent)));}
}
