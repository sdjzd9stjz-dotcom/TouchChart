import org.touchchart.remote.Wire;

public class WireTest {
    static void check(boolean condition,String name){if(!condition)throw new AssertionError(name);System.out.println("PASS "+name);}
    public static void main(String[] args)throws Exception{
        check(Wire.normalizeAddress("192.168.1.2").equals("http://192.168.1.2:8790"),"default desktop port");
        check(Wire.normalizeAddress("http://127.0.0.1:8788/").equals("http://127.0.0.1:8788"),"explicit server address");
        boolean rejected=false;try{Wire.normalizeAddress("user:secret@host:8790");}catch(IllegalArgumentException expected){rejected=true;}check(rejected,"invalid address rejected");
        check(Wire.synchronizedOffset(1000,1020,5010)==4000,"midpoint clock synchronization");
        check(Wire.normalized(-5,100)==0&&Wire.normalized(150,100)==1&&Wire.normalized(50,100)==.5f,"coordinates clamped");
        String base=args[0];check(Wire.request(base,"/api/sync",null).contains("now"),"real Java to Python HTTP sync");
        long stamp=System.currentTimeMillis();
        Wire.request(base,"/api/session/start","{\"bpm\":120,\"startEpoch\":"+stamp+"}");
        StringBuilder events=new StringBuilder();
        for(String kind:new String[]{"down","up"})for(int i=0;i<5;i++){
            if(events.length()>0)events.append(',');
            events.append("{\"k\":\"").append(kind).append("\",\"f\":").append(i).append(",\"x\":").append(.1+i*.18).append(",\"y\":0.5,\"t\":").append(stamp+(kind.equals("up")?150:100)).append('}');
        }
        String payload="{\"sid\":\"android-wire-test\",\"batch\":1,\"events\":["+events+"]}";
        check(Wire.request(base,"/api/events",payload).contains("true"),"five-finger batch accepted");
        check(Wire.request(base,"/api/events",payload).contains("duplicate"),"retry uses same batch without duplication");
        Wire.request(base,"/api/session/stop","{}");
        check(Wire.request(base,"/api/state",null).contains("\"maxMulti\": 5"),"native transport produces multi 5");
        check(Wire.request(base,"/api/chart",null).contains("judgeLineList"),"native transport produces exportable RPE");
        System.out.println("ANDROID JAVA TRANSPORT SELFTEST PASSED (10/10)");
    }
}
