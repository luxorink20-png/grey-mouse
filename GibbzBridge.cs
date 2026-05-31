// ╔══════════════════════════════════════════════════════════════════╗
//  GibbzBridge.cs  —  ATAS Indicator → UDP Bridge  v2.2
//
//  Función: Envía cada barra completada vía UDP a GIBBZ Python Engine.
//  Puerto:  127.0.0.1:9999 (configurable en parámetros del indicador)
//
//  PAYLOAD FORMAT (CSV, posiciones fijas):
//    0  Close   1  Open   2  High   3  Low   4  Close(dup)
//    5  Volume  6  Delta  7  AskVol 8  BidVol 9  Trades(0)
//   10  Timestamp(unix)  11  Symbol  12  BarIndex
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
//    Comandos: RECORD | STATUS | STOP
// ╚══════════════════════════════════════════════════════════════════╝

using ATAS.Indicators;
using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Globalization;

public class GibbzBridge : Indicator
{
    // ── Parámetros (editables en panel de ATAS) ──────────────────────
    public string UdpHost { get; set; } = "127.0.0.1";
    public int    UdpPort { get; set; } = 9999;

    // ── Estado interno ───────────────────────────────────────────────
    private UdpClient   _udp;
    private IPEndPoint  _ep;
    private int         _barsSent    = 0;
    private int         _lastBarSent = -1;
    private DateTime    _lastSend    = DateTime.MinValue;
    private string      _statusPath;
    private string      _cmdPath;
    private Timer       _pollTimer;
    private readonly object _sendLock = new object();

    // ── Inicialización ───────────────────────────────────────────────
    protected override void OnInitialize()
    {
        string home  = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        _statusPath  = Path.Combine(home, "gibbz_bridge_status.txt");
        _cmdPath     = Path.Combine(home, "gibbz_bridge_cmd.txt");

        InitUdp();
        WriteStatus("INIT");

        // Poll command file cada 500 ms en hilo separado (no bloquea OnCalculate)
        _pollTimer = new Timer(PollCommandFile, null,
            TimeSpan.FromSeconds(1), TimeSpan.FromMilliseconds(500));
    }

    // ── Cálculo por barra ────────────────────────────────────────────
    protected override void OnCalculate(int bar, decimal value)
    {
        // Solo enviar barras completadas (no la barra en formación)
        if (bar >= CurrentBar - 1) return;
        if (bar == _lastBarSent)   return;

        var candle = GetCandle(bar);
        if (candle == null || candle.Volume <= 0) return;

        _lastBarSent = bar;

        // Delta directo de ATAS (ya calculado por la plataforma)
        decimal delta  = candle.Delta;
        decimal askVol = Math.Max(0m, (candle.Volume + delta) / 2m);
        decimal bidVol = Math.Max(0m, (candle.Volume - delta) / 2m);

        // Timestamp Unix (segundos)
        long   ts     = ((DateTimeOffset)candle.Time).ToUnixTimeSeconds();
        string symbol = InstrumentInfo?.Instrument ?? "UNKNOWN";

        // Formato invariante (punto decimal, nunca coma)
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

                // Actualizar status cada 100 barras
                if (_barsSent % 100 == 0)
                    WriteStatus("STREAMING");
            }
            catch (Exception ex)
            {
                WriteStatus("SEND_ERROR:" + ex.Message);
                // Reintentar con socket fresco la próxima barra
                try { _udp?.Close(); } catch { }
                _udp = null;
            }
        }
    }

    // ── Leer comandos de Python ──────────────────────────────────────
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

    // ── Escribir status para Python ──────────────────────────────────
    private void WriteStatus(string msg)
    {
        try
        {
            string sym = InstrumentInfo?.Instrument ?? "?";
            string content = string.Format(
                "ts={0}\nstatus={1}\nbars_sent={2}\nsymbol={3}\nport={4}\nlast_send={5}\n",
                DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss"),
                msg, _barsSent, sym, UdpPort,
                _lastSend == DateTime.MinValue ? "never" : _lastSend.ToString("HH:mm:ss"));
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
