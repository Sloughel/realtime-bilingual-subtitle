package com.sloughel.bilingualsubtitle.overlay

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import com.sloughel.bilingualsubtitle.AppState

/**
 * 悬浮窗字幕（FR-08）：双语两行、可拖动、可调透明度、可关闭。
 *
 * 需要 SYSTEM_ALERT_WINDOW 授权；未授权时返回 false，由控制页提示去系统设置。
 * 所有 View 操作都切到主线程执行。
 */
@android.annotation.SuppressLint("StaticFieldLeak", "ClickableViewAccessibility")
object OverlayManager {
    private val main = Handler(Looper.getMainLooper())
    private var windowManager: WindowManager? = null
    private var container: LinearLayout? = null
    private var layoutParams: WindowManager.LayoutParams? = null
    private var sourceView: TextView? = null
    private var translationView: TextView? = null
    private var visible = false

    fun canDraw(context: Context): Boolean = Settings.canDrawOverlays(context)

    /** 请求显示悬浮窗；返回是否具备权限（真正的 addView 在主线程异步执行）。 */
    fun show(context: Context): Boolean {
        if (!canDraw(context)) return false
        val app = context.applicationContext
        main.post { addOverlay(app) }
        return true
    }

    private fun addOverlay(app: Context) {
        if (visible) return
        val wm = app.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        windowManager = wm
        val density = app.resources.displayMetrics.density
        fun dp(value: Int) = (value * density).toInt()

        val root = LinearLayout(app).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(12))
            background = GradientDrawable().apply {
                setColor(Color.argb(225, 16, 20, 28))
                cornerRadius = dp(14).toFloat()
            }
            alpha = AppState.state.value.overlayAlpha
        }

        val topRow = LinearLayout(app).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val sourceText = TextView(app).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            maxLines = 3
            text = "等待字幕…"
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }

        val closeButton = TextView(app).apply {
            text = "×"
            setTextColor(Color.parseColor("#FF8A80"))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
            setPadding(dp(12), 0, 0, 0)
            setOnClickListener {
                hide()
                AppState.setOverlay(false)
            }
        }

        val translationText = TextView(app).apply {
            setTextColor(Color.parseColor("#FFD54F"))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 17f)
            setPadding(0, dp(4), 0, 0)
            maxLines = 3
            text = ""
        }

        topRow.addView(sourceText)
        topRow.addView(closeButton)
        root.addView(topRow)
        root.addView(translationText)

        val metrics = app.resources.displayMetrics
        val params = WindowManager.LayoutParams(
            (metrics.widthPixels * 0.92f).toInt(),
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = dp(16)
            y = (metrics.heightPixels * 0.66f).toInt()
        }

        var startX = 0
        var startY = 0
        var downX = 0f
        var downY = 0f
        root.setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    startX = params.x
                    startY = params.y
                    downX = event.rawX
                    downY = event.rawY
                    true
                }

                MotionEvent.ACTION_MOVE -> {
                    params.x = startX + (event.rawX - downX).toInt()
                    params.y = startY + (event.rawY - downY).toInt()
                    runCatching { wm.updateViewLayout(root, params) }
                    true
                }

                else -> false
            }
        }

        val added = runCatching { wm.addView(root, params) }.isSuccess
        if (!added) {
            AppState.setError("悬浮窗添加失败（系统可能禁止显示在其他应用上层）")
            return
        }
        container = root
        layoutParams = params
        sourceView = sourceText
        translationView = translationText
        visible = true
    }

    fun hide() {
        val root = container ?: return
        main.post {
            runCatching { windowManager?.removeView(root) }
            container = null
            layoutParams = null
            sourceView = null
            translationView = null
            visible = false
        }
    }

    fun update(source: String, translation: String) {
        main.post {
            sourceView?.text = source
            translationView?.text = translation.ifBlank { if (source.isNotBlank()) "（翻译中…）" else "" }
        }
    }

    fun setAlpha(alpha: Float) {
        main.post { container?.alpha = alpha.coerceIn(0.2f, 1f) }
    }

    val isVisible: Boolean get() = visible
}
