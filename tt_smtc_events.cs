using System;
using System.Collections.Generic;
using System.Linq;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Windows.Foundation;
using Windows.Media.Control;

internal static class SessionEvents
{
    private static readonly AutoResetEvent Changed = new AutoResetEvent(false);
    private const int Sessions = 1, Media = 2, Playback = 4;
    private static int pending;
    private static readonly object Output = new object();

    private static void Emit(string line)
    {
        lock (Output) Console.WriteLine(line);
    }

    private static void ReadCommands(GlobalSystemMediaTransportControlsSessionManager manager, string mailbox)
    {
        string previous = "";
        while (true)
        {
                Thread.Sleep(35);
                string line;
                try { line = File.ReadAllText(mailbox, Encoding.ASCII); }
                catch (IOException) { continue; }
                if (line == previous) continue;
                previous = line;
                var fields = line.Split('\t');
                if (fields.Length != 2) continue;
                try
                {
                    var source = Encoding.UTF8.GetString(Convert.FromBase64String(fields[1]));
                    var matches = manager.GetSessions().Where(item =>
                        String.Equals(item.SourceAppUserModelId, source, StringComparison.OrdinalIgnoreCase)).ToArray();
                    var session = matches.Length == 1 ? matches[0] : null;
                    if (session == null) { Emit("timeline\t" + fields[0]); continue; }
                    var playback = session.GetPlaybackInfo();
                    var timeline = session.GetTimelineProperties();
                    var length = Math.Max(0, (timeline.EndTime - timeline.StartTime).TotalMilliseconds);
                    var position = (timeline.Position - timeline.StartTime).TotalMilliseconds;
                    double rate = playback.PlaybackRate ?? 1.0;
                    if (Double.IsNaN(rate) || Double.IsInfinity(rate) || rate <= 0) rate = 1.0;
                    double age = (DateTimeOffset.UtcNow - timeline.LastUpdatedTime).TotalMilliseconds;
                    bool timestampValid = timeline.LastUpdatedTime.Year > 2000 && age >= -1000 && age <= 300000;
                    double observed = playback.PlaybackStatus == GlobalSystemMediaTransportControlsSessionPlaybackStatus.Playing && timestampValid
                        ? position + Math.Max(0, age) * rate : position;
                    Emit(String.Format(CultureInfo.InvariantCulture, "timeline\t{0}\t{1}\t{2:F0}\t{3:F0}\t{4}\t{5}\t{6:F0}",
                        fields[0], playback.PlaybackStatus, Math.Max(0, Math.Min(length, observed)), length, rate,
                        timeline.LastUpdatedTime.ToUniversalTime().Ticks, position));
                }
                catch (Exception error)
                {
                    Console.Error.WriteLine(error.GetType().Name + " " + error.Message);
                    Emit("timeline\t" + fields[0]);
                }
        }
    }

    private static void Notify(int kind)
    {
        int previous;
        do { previous = pending; }
        while (Interlocked.CompareExchange(ref pending, previous | kind, previous) != previous);
        Changed.Set();
    }

    private static Action Listen(Type type, object target, string name, Delegate callback)
    {
        var token = type.GetMethod("add_" + name).Invoke(target, new object[] { callback });
        return () => type.GetMethod("remove_" + name).Invoke(target, new[] { token });
    }

    private static void WatchSessions(GlobalSystemMediaTransportControlsSessionManager manager, List<Action> subscriptions)
    {
        foreach (var remove in subscriptions)
        {
            // The session can already be disconnected when the list changes.
            try { remove(); } catch (System.Reflection.TargetInvocationException) { }
        }
        subscriptions.Clear();
        var type = typeof(GlobalSystemMediaTransportControlsSession);
        foreach (var session in manager.GetSessions())
        {
            try
            {
                subscriptions.Add(Listen(type, session, "MediaPropertiesChanged",
                    new TypedEventHandler<GlobalSystemMediaTransportControlsSession, MediaPropertiesChangedEventArgs>(
                        (sender, args) => Notify(Media))));
                subscriptions.Add(Listen(type, session, "PlaybackInfoChanged",
                    new TypedEventHandler<GlobalSystemMediaTransportControlsSession, PlaybackInfoChangedEventArgs>(
                        (sender, args) => Notify(Playback))));
            }
            catch (System.Reflection.TargetInvocationException) { }
        }
    }

    private static async Task Run(string mailbox)
    {
        var asTask = typeof(System.WindowsRuntimeSystemExtensions).GetMethods().First(method =>
            method.Name == "AsTask" && method.IsGenericMethodDefinition &&
            method.GetParameters().Length == 1 &&
            method.GetParameters()[0].ParameterType.Name == "IAsyncOperation`1");
        var task = (Task<GlobalSystemMediaTransportControlsSessionManager>)asTask
            .MakeGenericMethod(typeof(GlobalSystemMediaTransportControlsSessionManager))
            .Invoke(null, new object[] { GlobalSystemMediaTransportControlsSessionManager.RequestAsync() });
        var manager = await task;
        var sessions = new TypedEventHandler<GlobalSystemMediaTransportControlsSessionManager, SessionsChangedEventArgs>(
            (sender, args) => Notify(Sessions));
        var current = new TypedEventHandler<GlobalSystemMediaTransportControlsSessionManager, CurrentSessionChangedEventArgs>(
            (sender, args) => Notify(Sessions));
        var type = typeof(GlobalSystemMediaTransportControlsSessionManager);
        Listen(type, manager, "SessionsChanged", sessions);
        Listen(type, manager, "CurrentSessionChanged", current);
        var subscriptions = new List<Action>();
        WatchSessions(manager, subscriptions);
        var commands = new Thread(() => ReadCommands(manager, mailbox));
        commands.SetApartmentState(ApartmentState.MTA);
        commands.IsBackground = true;
        if (!String.IsNullOrEmpty(mailbox)) commands.Start();
        Emit("ready");
        while (true)
        {
            Changed.WaitOne();
            // Coalesce Windows notifications when several sessions change together.
            Thread.Sleep(180);
            var notices = Interlocked.Exchange(ref pending, 0);
            if ((notices & Sessions) != 0)
            {
                WatchSessions(manager, subscriptions);
                Emit("sessions");
            }
            if ((notices & Media) != 0) Emit("media");
            if ((notices & Playback) != 0) Emit("playback");
        }
    }

    private static int Main(string[] args)
    {
        var parentExit = new Thread(() => {
            using (var input = Console.OpenStandardInput()) { input.ReadByte(); }
            Environment.Exit(0);
        });
        parentExit.IsBackground = true;
        parentExit.Start();
        try { Run(args.Length == 1 ? args[0] : null).GetAwaiter().GetResult(); }
        catch (Exception error) { Console.Error.WriteLine(error.GetType().Name); return 1; }
        return 0;
    }
}
