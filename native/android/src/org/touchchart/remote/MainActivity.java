package org.touchchart.remote;

import android.app.Activity;
import android.os.*;
import android.content.*;
import android.graphics.*;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.util.*;
import java.util.concurrent.*;

public class MainActivity extends Activity {
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final ExecutorService sender=Executors.newSingleThreadExecutor();
    private final ScheduledExecutorService control=Executors.newSingleThreadScheduledExecutor();
    private final ArrayDeque<JSONObject> queue=new ArrayDeque<>();
    private final Object queueLock=new Object();
    private volatile boolean connected=false,connecting=false,wanted=false,destroyed=false,suspended=false;
    private volatile String base="",sid=UUID.randomUUID().toString();
    private volatile double offset=0,rtt=0;
    private boolean sending=false;
    private long batch=0,lastMove=0;
    private EditText address;
    private TextView status,stats;
    private Button connect;
    private TouchSurface surface;
    private final int background=Color.rgb(13,20,34),cyan=Color.rgb(107,220,237);

    private int dp(float value){return Math.round(value*getResources().getDisplayMetrics().density);}
    private void show(String message){ui.post(()->{if(!destroyed)status.setText(message);});}
    private double now(){return SystemClock.uptimeMillis();}
    private JSONObject object(String text)throws JSONException{return new JSONObject(text);}
    private JSONObject request(String route,JSONObject body)throws Exception{return object(Wire.request(base,route,body==null?null:body.toString()));}
    @Override public void onCreate(Bundle saved){
        super.onCreate(saved);getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().setStatusBarColor(background);getWindow().setNavigationBarColor(background);
        LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(background);root.setPadding(dp(14),dp(10),dp(14),dp(10));
        root.setOnApplyWindowInsetsListener((view,insets)->{view.setPadding(dp(14)+insets.getSystemWindowInsetLeft(),dp(10)+insets.getSystemWindowInsetTop(),dp(14)+insets.getSystemWindowInsetRight(),dp(10)+insets.getSystemWindowInsetBottom());return insets;});
        TextView title=new TextView(this);title.setText("TouchChart  /  触控台");title.setTextColor(Color.WHITE);title.setTextSize(25);title.setPadding(0,dp(6),0,dp(12));root.addView(title);
        LinearLayout row=new LinearLayout(this);
        address=new EditText(this);address.setTextColor(Color.WHITE);address.setSingleLine();address.setTextSize(16);address.setHintTextColor(Color.GRAY);address.setHint("电脑显示的 IP:8790");
        address.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_URI);
        address.setText(getPreferences(0).getString("address",""));row.addView(address,new LinearLayout.LayoutParams(0,dp(54),1));
        connect=new Button(this);connect.setText("连接");row.addView(connect);root.addView(row);
        status=new TextView(this);status.setText("同一 Wi-Fi · 在电脑开始录制后触摸下方区域");status.setTextColor(cyan);status.setTextSize(14);status.setPadding(0,dp(8),0,dp(8));root.addView(status);
        surface=new TouchSurface(this);root.addView(surface,new LinearLayout.LayoutParams(-1,0,1));
        stats=new TextView(this);stats.setTextColor(Color.LTGRAY);stats.setTextSize(14);stats.setText("最多五指 · Tap / Hold / Flick / Drag · 8ms 移动采样");stats.setPadding(0,dp(12),0,dp(10));root.addView(stats);
        Button release=new Button(this);release.setText("释放全部手指 / 重新同步");release.setOnClickListener(v->{surface.release();connected=false;control.execute(this::synchronize);});root.addView(release);
        TextView hint=new TextView(this);hint.setText("音频播放、BPM 与导出在桌面软件控制。此应用不上传云端、不申请麦克风或存储权限。");hint.setTextColor(Color.GRAY);hint.setTextSize(12);root.addView(hint);
        setContentView(root);
        connect.setOnClickListener(v->{
            if(wanted){disconnect();return;}
            try{base=Wire.normalizeAddress(address.getText().toString());getPreferences(0).edit().putString("address",address.getText().toString()).apply();wanted=true;connect.setText("断开");address.setEnabled(false);control.execute(this::synchronize);}
            catch(Exception error){show(error.getMessage());}
        });
        control.scheduleWithFixedDelay(()->{
            if(!wanted||destroyed||suspended)return;
            if(!connected){synchronize();return;}
            try{
                JSONObject data=request("/api/events?since=2147483647",null);JSONObject count=data.getJSONObject("stats");
                boolean recording=data.getBoolean("recording"),paused=data.getBoolean("paused");
                show((recording?(paused?"已暂停":"● 录制中"):"已连接 · 等待电脑开始录制")+String.format(Locale.US," · RTT %.1fms",rtt));
                String detail="音符 "+count.getInt("total")+"  Tap "+count.getInt("tap")+" / Hold "+count.getInt("hold")+" / Flick "+count.getInt("flick")+" / Drag "+count.getInt("drag");
                ui.post(()->{if(!destroyed)stats.setText(detail);});
            }catch(Exception error){lost(error);}
        },1,1,TimeUnit.SECONDS);
    }
    private void synchronize(){
        if(!wanted||connecting||destroyed||suspended)return;
        connecting=true;connected=false;show("正在同步时钟…");
        synchronized(queueLock){queue.clear();}
        try {
            // Finish any in-flight batch before releasing its fingers and accepting a new session.
            sender.submit(()->{}).get(15,TimeUnit.SECONDS);
            double best=Double.MAX_VALUE,bestOffset=0;
            for(int i=0;i<5;i++){double before=now();JSONObject response=request("/api/sync",null);double after=now();if(after-before<best){best=after-before;bestOffset=Wire.synchronizedOffset(before,after,response.getDouble("now"));}}
            request("/api/client/reset",new JSONObject().put("sid",sid));
            offset=bestOffset;rtt=best;connected=wanted&&!destroyed;
            if(connected){ui.post(()->surface.clearPoints());show(String.format(Locale.US,"已连接 · RTT %.1fms · 可以开始触控",rtt));}
        }catch(Exception error){lost(error);}finally{connecting=false;}
    }
    private void lost(Exception error){
        connected=false;
        synchronized(queueLock){queue.clear();}
        show("连接失败，将自动重试："+error.getMessage());
        ui.post(()->{if(!destroyed)surface.clearPoints();});
    }
    private void disconnect(){
        surface.release();wanted=false;connected=false;
        synchronized(queueLock){queue.clear();}
        control.execute(()->{try{sender.submit(()->{}).get(15,TimeUnit.SECONDS);request("/api/client/reset",new JSONObject().put("sid",sid));}catch(Exception ignored){}});
        connect.setText("连接");address.setEnabled(true);show("已断开 · 当前输入不会录入");
    }
    private void enqueue(String kind,int id,float x,float y,long eventTime){
        if(!connected)return;
        try{
            JSONObject event=new JSONObject().put("k",kind).put("f",id).put("x",Wire.normalized(x,surface.getWidth())).put("y",Wire.normalized(y,surface.getHeight())).put("t",eventTime+offset);
            synchronized(queueLock){
                if(queue.size()>=10000){queue.clear();connected=false;show("输入队列溢出，已停止发送，请重新同步");return;}
                queue.add(event);
                if(!sending){sending=true;sender.execute(this::drain);}
            }
        }catch(Exception error){lost(error);}
    }
    private void drain(){
        while(true){
            JSONArray events=new JSONArray();long id;
            synchronized(queueLock){
                if(queue.isEmpty()||!connected){sending=false;return;}
                while(!queue.isEmpty()&&events.length()<1000)events.put(queue.removeFirst());
                id=++batch;
            }
            try{
                JSONObject body=new JSONObject().put("sid",sid).put("batch",id).put("events",events);
                Exception failure=null;
                for(int attempt=0;attempt<2;attempt++){try{request("/api/events",body);failure=null;break;}catch(Exception error){failure=error;}}
                if(failure!=null)throw failure;
            }catch(Exception error){lost(error);synchronized(queueLock){sending=false;}return;}
        }
    }
    @Override protected void onPause(){
        suspended=true;
        if(surface!=null)surface.release();
        // Release first; reconnect creates a clean session after backgrounding.
        if(wanted){connected=false;synchronized(queueLock){queue.clear();}control.execute(()->{try{sender.submit(()->{}).get(15,TimeUnit.SECONDS);request("/api/client/reset",new JSONObject().put("sid",sid));}catch(Exception ignored){}});}
        super.onPause();
    }
    @Override protected void onResume(){super.onResume();suspended=false;}
    @Override protected void onDestroy(){
        wanted=false;destroyed=true;connected=false;control.shutdownNow();sender.shutdownNow();super.onDestroy();
    }
    private final class TouchSurface extends View {
        private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
        private final LinkedHashMap<Integer,PointF> points=new LinkedHashMap<>();
        private final int[] colors={0xff62d8ef,0xfff29bc7,0xfff4d36f,0xff89e1b6,0xffb4a2f5};
        TouchSurface(Context context){super(context);setFocusable(true);setContentDescription("五指触控录制区域");}
        void clearPoints(){points.clear();invalidate();}
        void release(){long t=SystemClock.uptimeMillis();for(Map.Entry<Integer,PointF> p:points.entrySet())enqueue("cancel",p.getKey(),p.getValue().x,p.getValue().y,t);clearPoints();}
        @Override public boolean onTouchEvent(android.view.MotionEvent event){
            int action=event.getActionMasked(),index=event.getActionIndex(),id=event.getPointerId(index);long t=event.getEventTime();
            if(!connected){show("尚未连接，输入不会录入");return true;}
            if(action==MotionEvent.ACTION_DOWN||action==MotionEvent.ACTION_POINTER_DOWN){
                if(points.size()<5){PointF p=new PointF(event.getX(index),event.getY(index));points.put(id,p);enqueue("down",id,p.x,p.y,t);performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP);}
            }else if(action==MotionEvent.ACTION_MOVE){
                boolean send=t-lastMove>=8;
                for(int i=0;i<event.getPointerCount();i++){int pointer=event.getPointerId(i);PointF p=points.get(pointer);if(p!=null){p.set(event.getX(i),event.getY(i));if(send)enqueue("move",pointer,p.x,p.y,t);}}
                if(send)lastMove=t;
            }else if(action==MotionEvent.ACTION_UP||action==MotionEvent.ACTION_POINTER_UP){
                PointF p=points.remove(id);if(p!=null)enqueue("up",id,event.getX(index),event.getY(index),t);
                if(action==MotionEvent.ACTION_UP)performClick();
            }else if(action==MotionEvent.ACTION_CANCEL){release();}
            invalidate();return true;
        }
        @Override public boolean performClick(){super.performClick();return true;}
        @Override protected void onDraw(Canvas canvas){
            super.onDraw(canvas);canvas.drawColor(0xff111d30);
            paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(dp(1));paint.setColor(0xff26374e);
            for(int i=1;i<5;i++)canvas.drawLine(getWidth()*i/5f,0,getWidth()*i/5f,getHeight(),paint);
            paint.setColor(0xfff3d885);paint.setStrokeWidth(dp(2));canvas.drawLine(0,getHeight()*.86f,getWidth(),getHeight()*.86f,paint);
            if(points.isEmpty()){
                paint.setStyle(Paint.Style.FILL);paint.setColor(0xff6686a7);paint.setTextSize(dp(21));paint.setTextAlign(Paint.Align.CENTER);
                canvas.drawText(connected?"在这里跟着节奏敲击":"连接电脑后开始",getWidth()/2f,getHeight()/2f,paint);
                paint.setTextSize(dp(13));canvas.drawText("按住 · 快划 · 连续滑动 · 最多五指",getWidth()/2f,getHeight()/2f+dp(30),paint);
            }
            int slot=0;
            for(Map.Entry<Integer,PointF> entry:points.entrySet()){
                PointF p=entry.getValue();paint.setColor(colors[slot%5]);paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(dp(3));canvas.drawCircle(p.x,p.y,dp(32),paint);
                paint.setStyle(Paint.Style.FILL);paint.setTextSize(dp(22));paint.setTextAlign(Paint.Align.CENTER);canvas.drawText(String.valueOf(++slot),p.x,p.y+dp(8),paint);
            }
        }
    }
}
