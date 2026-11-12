package kr.eartalk.car;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import ai.pleos.playground.stt.SpeechToText;
import ai.pleos.playground.stt.listener.ResultListener;
import ai.pleos.playground.tts.TextToSpeech;
import java.util.Locale;
import java.util.function.Consumer;

/** Owns a recognition request until its final result, cancellation, or timeout. */
final class PleosSpeech {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final TextToSpeech output;
    private final SpeechToText input;
    private final Consumer<String> notice;
    private ResultListener current;
    private boolean closed, listeningReady, speakingReady;
    private final Runnable timeout = () -> finish(current, null);
    private final ai.pleos.playground.tts.listener.EventListener playback =
            new ai.pleos.playground.tts.listener.EventListener() {
        public void onStart() { Log.d("EarTalkVoice","vehicle playback started"); }
        public void onReady() {}
        public void onDone() { Log.d("EarTalkVoice","vehicle playback finished"); }
        public void onStop() {}
        public void onUpdatedRms(double rms) {}
        public void onError(ai.pleos.playground.tts.constant.ErrorType error) {
            main.post(() -> report("음성 출력 오류 · " + error));
        }
    };

    private void report(String message) {
        if (!closed) {
            Log.w("EarTalkVoice",message);
            notice.accept(message);
        }
    }

    PleosSpeech(Context context, Consumer<String> notice) {
        this.notice = notice;
        input = new SpeechToText(context, ai.pleos.playground.stt.constant.Mode.ON_DEVICE);
        output = new TextToSpeech(context, ai.pleos.playground.tts.constant.Mode.ON_DEVICE);
        input.initialize(new ai.pleos.playground.stt.listener.OnInitializeListener() {
            public void onSuccess() { main.post(() -> {
                listeningReady = !closed;
                if (!closed) Log.d("EarTalkVoice","recognizer initialized");
            }); }
            public void onFailure(ai.pleos.playground.stt.constant.ErrorType error) {
                main.post(() -> report("음성 인식 초기화 실패 · " + error));
            }
        });
        output.initialize(new ai.pleos.playground.tts.listener.OnInitializeListener() {
            public void onSuccess() { main.post(() -> {
                if (!closed) {
                    output.addEventListener(playback);
                    speakingReady = true;
                    Log.d("EarTalkVoice","synthesizer initialized");
                }
            }); }
            public void onFailure(ai.pleos.playground.tts.constant.ErrorType error) {
                main.post(() -> report("음성 출력 초기화 실패 · " + error));
            }
        });
    }

    boolean canSpeak() { return speakingReady; }
    void speak(String phrase, Locale locale) {
        output.setLocale(locale);
        output.speak(phrase, true);
    }

    private Consumer<String> answer;
    boolean listen(Consumer<String> callback) {
        if (!listeningReady || current != null) return false;
        input.setLocale(Locale.US);
        answer = callback;
        current = new ResultListener() {
            public void onReady() {}
            public void onStartedRecognition() {}
            public void onUpdatedRms(double rms) {}
            public void onUpdatedEpdData(long on, long off) {}
            public void onUpdated(String transcript, boolean complete) {
                if (complete) main.post(() -> finish(this, transcript));
            }
            public void onEndedRecognition() { main.post(() -> finish(this, null)); }
            public void onError(ai.pleos.playground.stt.constant.ErrorType error) {
                main.post(() -> { if (current == this) { notice.accept("음성 인식 오류 · " + error); finish(this, null); } });
            }
        };
        input.addListener(current);
        main.postDelayed(timeout, 20000);
        input.request();
        return true;
    }

    private void finish(ResultListener request, String transcript) {
        if (request == null || request != current) return;
        Consumer<String> callback = answer;
        stopListening();
        callback.accept(transcript);
    }
    private void stopListening() {
        if (current == null) return;
        ResultListener old = current;
        current = null;
        answer = null;
        main.removeCallbacks(timeout);
        input.removeListener(old);
        input.stop();
    }
    void stop() {
        stopListening();
        if (speakingReady) output.stop();
    }
    void close() {
        closed = true;
        stop();
        if (speakingReady) output.removeEventListener(playback);
        input.release();
        output.release();
    }
}
