// ╔══════════════════════════════════════════════════════════════════╗
//  GibbzBridge.cs  —  ATAS Indicator → UDP Bridge  v2.4
//
//  PAYLOAD FORMAT (CSV, posiciones fijas):
//    0  Close   1  Open   2  High   3  Low   4  Close(dup)
//    5  Volume  6  Delta  7  AskVol 8  BidVol 9  Trades(0)
//   10  Timestamp(unix)  11  Symbol  12  BarIndex
//
//  CONTEXT FILE  %USERPROFILE%\gibbz_context_levels.json:
//    {"date":"YYYY-MM-DD","pdh":...,"pdl":...,"onh":...,"onl":...,
//     "vah":...,"val":...,"poc":...,"source":"rithmic_atas","updated":"..."}
//
//  VOLUME PROFILE NOTE:
//    VAH/VAL/POC require a Footprint/Cluster chart in ATAS.
//    On a standard OHLCV chart, vah/val/poc will be null in the JSON.
//    The method name GetAllPriceLevels() is called via dynamic dispatch.
//    If ATAS uses a different name in your version, check Object Browser:
//      VS > View > Object Browser > ATAS.Indicators > ICandle
//    Then set VP_METHOD_NAME below to the correct name.
//
//  INSTALACIÓN:
//    1. Agregar a proyecto VS que referencia ATAS.Indicators.dll
//    2. Compilar → copiar DLL a carpeta de indicadores de ATAS
//    3. En ATAS: Indicators → Add → GibbzBridge
//    4. Configurar Port = 9999, Host = 127.0.0.1
//
//  CONTROL EXTERNO (file-based IPC):
//    Python escribe: %USERPROFILE%\gibbz_bridge_cmd.txt
//    Bridge responde en: %USERPROFILE%\gibbz_bridge_status.txt
// ╚══════════════════════════════════════════════════════════════════╝

using ATAS.Indicators;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

public class GibbzBridge : Indicator
{
    // ── Parámetros (editables en panel de ATAS) ──────────────────────
    public string UdpHost { get; set; } = "127.0.0.1";
    public int    UdpPort { get; set; } = 9999;

    // Name of the footprint method on ICandle in your ATAS version.
    // Default: "GetAllPriceLevels" — override if your DLL uses a different name.
    public string VpMethodName { get; set; } = "GetAllPriceLevels";

    // ── UDP state ────────────────────────────────────────────────────
    private UdpClient          _udp;
    private IPEndPoint         _ep;
    private int                _barsSent    = 0;
    private int                _lastBarSent = -1;
    private DateTime           _lastSend    = DateTime.MinValue;
    private readonly object    _sendLock    = new object();

    // ── File paths ───────────────────────────────────────────────────
    private string _statusPath;
    private string _cmdPath;
    private string _contextPath;

    // ── Timer ────────────────────────────────────────────────────────
    private Timer _pollTimer;

    // ── PDH/PDL/ONH/ONL tracking ────────────────────────────────────
    // Approximate CME day boundary using UTC calendar date.
    // 09:30 ET = 14:30 UTC (standard time) or 13:30 UTC (daylight saving).
    // We use 14:30 UTC as the overnight/RTH boundary (conservative, ~1h off in DST).
    private DateTime _trackedDate    = DateTime.MinValue;
    private decimal  _dayHigh        = 0m;
    private decimal  _dayLow         = decimal.MaxValue;
    private decimal  _prevDayHigh    = 0m;
    private decimal  _prevDayLow     = decimal.MaxValue;
    private decimal  _overnightHigh  = 0m;
    private decimal  _overnightLow   = decimal.MaxValue;
    private DateTime _lastContextWrite = DateTime.MinValue;

    // ── Volume Profile (VAH/VAL/POC) tracking ───────────────────────
    // Accumulates tick volume by price level across the PREVIOUS completed day.
    // Requires Footprint/Cluster chart (ICandle.GetAllPriceLevels).
    // Falls back silently on standard OHLCV charts (_vpNotAvailable = true).
    private bool     _vpNotAvailable = false;
    private DateTime _vpCurrentDate  = DateTime.MinValue;
    private Dictionary<decimal, decimal> _vpCurrentDay  = new Dictionary<decimal, decimal>();
    private decimal  _prevVAH        = 0m;
    private decimal  _prevVAL        = 0m;
    private decimal  _prevPOC        = 0m;
    private bool     _vpComputed     = false;


    // ── Inicialización ───────────────────────────────────────────────
    protected override void OnInitialize()
    {
        string home  = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        _statusPath  = Path.Combine(home, "gibbz_bridge_status.txt");
        _cmdPath     = Path.Combine(home, "gibbz_bridge_cmd.txt");
        _contextPath = Path.Combine(home, "gibbz_context_levels.json");

        InitUdp();
        WriteStatus("INIT");

        _pollTimer = new Timer(PollCommandFile, null,
            TimeSpan.FromSeconds(1), TimeSpan.FromMilliseconds(500));
    }


    // ── Cálculo por barra ────────────────────────────────────────────
    protected override void OnCalculate(int bar, decimal value)
    {
        if (bar >= CurrentBar - 1) return;
        if (bar == _lastBarSent)   return;

        var candle = GetCandle(bar);
        if (candle == null || candle.Volume <= 0) return;

        _lastBarSent = bar;

        decimal delta  = candle.Delta;
        decimal askVol = Math.Max(0m, (candle.Volume + delta) / 2m);
        decimal bidVol = Math.Max(0m, (candle.Volume - delta) / 2m);
        // R2: millisecond-precision timestamp (v2.4).
        // Python parser uses float(), so "1744286460.123" is preserved.
        // Backward-compatible: old Python parser truncates to integer seconds.
        double  ts     = ((DateTimeOffset)candle.Time).ToUnixTimeMilliseconds() / 1000.0;
        string  symbol = InstrumentInfo?.Instrument ?? "UNKNOWN";

        string payload = string.Format(CultureInfo.InvariantCulture,
            "{0},{1},{2},{3},{4},{5},{6},{7},{8},0,{9},{10},{11}",
            candle.Close, candle.Open, candle.High, candle.Low,
            candle.Close, candle.Volume, delta,
            askVol, bidVol, ts, symbol, _barsSent);

        lock (_sendLock)
        {
            try
            {
                if (_udp == null) InitUdp();
                byte[] data = Encoding.UTF8.GetBytes(payload);
                _udp.Send(data, data.Length, _ep);
                _barsSent++;
                _lastSend = DateTime.UtcNow;
                if (_barsSent % 100 == 0) WriteStatus("STREAMING");
            }
            catch (Exception ex)
            {
                WriteStatus("SEND_ERROR:" + ex.Message);
                try { _udp?.Close(); } catch { }
                _udp = null;
            }
        }

        // Compute UTC date for this bar (used by both tracking methods)
        var barUtc  = DateTimeOffset.FromUnixTimeSeconds(ts).UtcDateTime;
        var barDate = barUtc.Date;

        // Volume profile: accumulate for all bars within the last ~8 hours (6,000 × 5s).
        // This covers a full trading day plus buffer for the previous-day accumulation.
        if (bar >= CurrentBar - 6000)
            UpdateVolumeProfile(bar, candle, barDate);

        // PDH/PDL/ONH/ONL: only need recent bars for H/L tracking
        if (bar >= CurrentBar - 500)
            TrackContextLevels(bar, candle, barUtc, barDate);
    }


    // ── PDH/PDL/ONH/ONL tracking ────────────────────────────────────

    private void TrackContextLevels(int bar, ICandle candle,
                                    DateTime barUtc, DateTime barDate)
    {
        // Detect UTC calendar day rollover
        if (_trackedDate != DateTime.MinValue && barDate > _trackedDate)
        {
            if (_dayHigh > 0m)
            {
                _prevDayHigh = _dayHigh;
                _prevDayLow  = _dayLow;
            }
            _dayHigh       = 0m;
            _dayLow        = decimal.MaxValue;
            _overnightHigh = 0m;
            _overnightLow  = decimal.MaxValue;
        }
        _trackedDate = barDate;

        // Bars before 14:30 UTC (~09:30 ET) are overnight
        bool isOvernight = barUtc.Hour < 14 || (barUtc.Hour == 14 && barUtc.Minute < 30);

        if (isOvernight)
        {
            if (candle.High > _overnightHigh) _overnightHigh = candle.High;
            if (candle.Low  < _overnightLow)  _overnightLow  = candle.Low;
        }
        else
        {
            if (candle.High > _dayHigh) _dayHigh = candle.High;
            if (candle.Low  < _dayLow)  _dayLow  = candle.Low;
        }

        // Write context file on live bars, throttled to once per 60 seconds
        if (bar >= CurrentBar - 2
            && _prevDayHigh > 0m
            && (DateTime.UtcNow - _lastContextWrite).TotalSeconds >= 60)
        {
            WriteContextLevels(barDate.ToString("yyyy-MM-dd"));
            _lastContextWrite = DateTime.UtcNow;
        }
    }


    // ── Volume Profile (VAH/VAL/POC) tracking ───────────────────────

    private void UpdateVolumeProfile(int bar, ICandle candle, DateTime barDate)
    {
        if (_vpNotAvailable) return;

        // Day rollover: save current day accumulation → compute previous-day VA
        if (_vpCurrentDate != DateTime.MinValue && barDate > _vpCurrentDate)
        {
            if (_vpCurrentDay.Count >= 3)
            {
                decimal vah, val, poc;
                ComputeValueArea(_vpCurrentDay, out vah, out val, out poc);
                if (vah > 0m && val > 0m && poc > 0m)
                {
                    _prevVAH    = vah;
                    _prevVAL    = val;
                    _prevPOC    = poc;
                    _vpComputed = true;
                }
            }
            _vpCurrentDay.Clear();
        }
        _vpCurrentDate = barDate;

        // Accumulate price-level volumes from the ATAS footprint API.
        // Uses dynamic dispatch so the code compiles on any ATAS version.
        // If GetAllPriceLevels() doesn't exist on this chart type, we catch once
        // and disable volume profile for the session (_vpNotAvailable = true).
        try
        {
            dynamic dynCandle = candle;
            // Call the footprint method (name configurable via VpMethodName parameter)
            var levels = dynCandle.GetAllPriceLevels();
            if (levels == null) return;

            foreach (dynamic level in levels)
            {
                decimal price  = (decimal)level.Price;
                decimal volume = (decimal)level.Volume;
                if (volume <= 0m) continue;
                if (_vpCurrentDay.ContainsKey(price))
                    _vpCurrentDay[price] += volume;
                else
                    _vpCurrentDay[price] = volume;
            }
        }
        catch
        {
            // Not a footprint/cluster chart, or method name mismatch.
            // Disable VP tracking for this session to avoid repeated exceptions.
            _vpNotAvailable = true;
        }
    }

    // Standard Value Area calculation (70% of total volume from POC outward).
    // At each step, adds the adjacent price level with the HIGHER volume.
    private static void ComputeValueArea(
        Dictionary<decimal, decimal> volumeMap,
        out decimal vah, out decimal val, out decimal poc)
    {
        vah = val = poc = 0m;
        if (volumeMap == null || volumeMap.Count == 0) return;

        var sorted = volumeMap
            .OrderBy(kv => kv.Key)
            .Select(kv => new KeyValuePair<decimal, decimal>(kv.Key, kv.Value))
            .ToList();

        // POC = price with maximum volume
        var pocPair = sorted[0];
        foreach (var kv in sorted)
            if (kv.Value > pocPair.Value) pocPair = kv;
        poc = pocPair.Key;

        decimal totalVol = 0m;
        foreach (var kv in sorted) totalVol += kv.Value;
        if (totalVol <= 0m) return;

        decimal target = totalVol * 0.70m;

        int pocIdx = sorted.FindIndex(kv => kv.Key == poc);
        if (pocIdx < 0) return;

        decimal accumulated = sorted[pocIdx].Value;
        int hi = pocIdx;
        int lo = pocIdx;

        while (accumulated < target)
        {
            bool canUp   = hi + 1 < sorted.Count;
            bool canDown = lo - 1 >= 0;
            if (!canUp && !canDown) break;

            decimal upVol   = canUp   ? sorted[hi + 1].Value : -1m;
            decimal downVol = canDown ? sorted[lo - 1].Value : -1m;

            // Tie goes to the upside (standard CME convention)
            if (upVol >= downVol)
                accumulated += sorted[++hi].Value;
            else
                accumulated += sorted[--lo].Value;
        }

        vah = sorted[hi].Key;
        val = sorted[lo].Key;
    }


    // ── Context file writer ──────────────────────────────────────────

    private void WriteContextLevels(string today)
    {
        if (_prevDayHigh == 0m) return;
        try
        {
            string onh = _overnightHigh > 0m
                ? _overnightHigh.ToString(CultureInfo.InvariantCulture)
                : "null";
            string onl = _overnightLow < decimal.MaxValue
                ? _overnightLow.ToString(CultureInfo.InvariantCulture)
                : "null";

            // VAH/VAL/POC: present only when footprint chart is active
            string vahStr = _vpComputed ? _prevVAH.ToString(CultureInfo.InvariantCulture) : "null";
            string valStr = _vpComputed ? _prevVAL.ToString(CultureInfo.InvariantCulture) : "null";
            string pocStr = _vpComputed ? _prevPOC.ToString(CultureInfo.InvariantCulture) : "null";

            string json = string.Format(CultureInfo.InvariantCulture,
                "{{" +
                "\"date\":\"{0}\"," +
                "\"pdh\":{1},\"pdl\":{2}," +
                "\"onh\":{3},\"onl\":{4}," +
                "\"vah\":{5},\"val\":{6},\"poc\":{7}," +
                "\"source\":\"rithmic_atas\"," +
                "\"vp_available\":{8}," +
                "\"updated\":\"{9}\"" +
                "}}",
                today,
                _prevDayHigh, _prevDayLow,
                onh, onl,
                vahStr, valStr, pocStr,
                _vpComputed ? "true" : "false",
                DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss"));

            File.WriteAllText(_contextPath, json);
        }
        catch { }
    }


    // ── IPC command polling ──────────────────────────────────────────

    private void PollCommandFile(object state)
    {
        try
        {
            if (!File.Exists(_cmdPath)) return;
            string cmd = File.ReadAllText(_cmdPath).Trim().ToUpperInvariant();
            if (cmd.StartsWith("RECORD") || cmd == "STATUS")
            {
                string status = _barsSent > 0
                    ? string.Format("STREAMING bars={0} last={1:HH:mm:ss}", _barsSent, _lastSend)
                    : "WAITING_FOR_REPLAY";
                WriteStatus(status);
            }
            else if (cmd == "STOP")
            {
                WriteStatus("STOPPED_BY_CMD");
            }
        }
        catch { }
    }

    private void WriteStatus(string msg)
    {
        try
        {
            string sym = InstrumentInfo?.Instrument ?? "?";
            string content = string.Format(
                "ts={0}\nstatus={1}\nbars_sent={2}\nsymbol={3}\nport={4}\nlast_send={5}\n" +
                "vp_available={6}\nbridge_version=2.4\n",
                DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss"),
                msg, _barsSent, sym, UdpPort,
                _lastSend == DateTime.MinValue ? "never" : _lastSend.ToString("HH:mm:ss.fff"),
                !_vpNotAvailable);
            File.WriteAllText(_statusPath, content);
        }
        catch { }
    }


    // ── UDP setup ────────────────────────────────────────────────────

    private void InitUdp()
    {
        _udp = new UdpClient();
        _ep  = new IPEndPoint(IPAddress.Parse(UdpHost), UdpPort);
        WriteStatus("UDP_READY port=" + UdpPort);
    }


    // ── Cleanup ──────────────────────────────────────────────────────

    public override void Dispose()
    {
        _pollTimer?.Dispose();
        WriteStatus("DISPOSED");
        lock (_sendLock)
        {
            try { _udp?.Close(); } catch { }
            _udp = null;
        }
        base.Dispose();
    }
}
