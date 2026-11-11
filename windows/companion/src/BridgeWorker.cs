using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Threading;

namespace OmoBench.Companion
{
    // Private stdio worker, no listener, configuration, registration or logs.
    // All connections go to the one literal Windows loopback port in argv.
    internal sealed class BridgeWorker
    {
        private const int Chunk = 16384, Window = 65536;
        private readonly Stream input, output;
        private readonly int port;
        private readonly object sendGate = new object();
        private readonly ConcurrentDictionary<uint, Channel> channels = new ConcurrentDictionary<uint, Channel>();
        private uint lastId;
        private volatile bool stopped;

        internal BridgeWorker(Stream input, Stream output, int port)
        { this.input = input; this.output = output; this.port = port; }

        internal static int Run(int port)
        {
            if (port < 1 || port > 65535) return 2;
            try { return new BridgeWorker(Console.OpenStandardInput(), Console.OpenStandardOutput(), port).Run(); }
            catch { return 1; } // Never write an exception into the framed pipe or diagnostics.
        }

        private static uint Number(byte[] b, int start)
        { return ((uint)b[start] << 24) | ((uint)b[start + 1] << 16) | ((uint)b[start + 2] << 8) | b[start + 3]; }
        private static void Put(byte[] b, int start, uint value)
        { for (int i = 3; i >= 0; i--) { b[start + i] = (byte)value; value >>= 8; } }
        private byte[] Read(int length)
        {
            byte[] bytes = new byte[length];
            int count = 0;
            while (count < length)
            {
                int got = input.Read(bytes, count, length - count);
                if (got == 0) throw new EndOfStreamException();
                count += got;
            }
            return bytes;
        }
        private void Send(byte kind, uint id, byte[] data)
        {
            lock (sendGate)
            {
                if (stopped) return;
                byte[] header = new byte[9]; header[0] = kind;
                Put(header, 1, id); Put(header, 5, (uint)data.Length);
                output.Write(header, 0, header.Length);
                output.Write(data, 0, data.Length); output.Flush();
            }
        }
        private void Credit(uint id, int count)
        { byte[] b = new byte[4]; Put(b, 0, (uint)count); Send(4, id, b); }

        internal int Run()
        {
            try
            {
                ThreadPool.SetMinThreads(64, 64);
                output.Write(new byte[] { 79, 77, 66, 49 }, 0, 4); output.Flush();
                while (true)
                {
                    byte[] header = Read(9);
                    byte kind = header[0]; uint id = Number(header, 1), length = Number(header, 5);
                    if (length > Chunk || id == 0) return 2;
                    byte[] data = Read((int)length);
                    Channel channel;
                    if (kind == 1 && length == 0)
                    {
                        if (id <= lastId) return 2;
                        lastId = id;
                        if (channels.Count >= 32) { Send(5, id, new byte[0]); continue; }
                        channel = new Channel(this, id);
                        channels[id] = channel;
                        ThreadPool.QueueUserWorkItem(delegate { channel.Start(); });
                    }
                    else if (id > lastId) return 2;
                    else if (channels.TryGetValue(id, out channel))
                    {
                        if (kind == 3 && length > 0) channel.Accept(data);
                        else if (kind == 4 && length == 4) channel.AddCredit(Number(data, 0));
                        else if (kind == 5 && length == 0) channel.Close();
                        else return 2;
                    }
                }
            }
            catch (EndOfStreamException) { return 0; }
            catch { return 1; }
            finally
            {
                stopped = true;
                foreach (Channel channel in channels.Values) channel.Close();
            }
        }

        private sealed class Channel
        {
            private readonly BridgeWorker owner;
            private readonly uint id;
            private readonly TcpClient client = new TcpClient(AddressFamily.InterNetwork);
            private readonly byte[] incoming = new byte[Window];
            private int head, tail, count;
            private readonly object gate = new object();
            private int credit = Window, receiveCredit = Window;
            private bool closed, ready;
            internal Channel(BridgeWorker owner, uint id) { this.owner = owner; this.id = id; }
            internal void Start()
            {
                try
                {
                    IAsyncResult connecting = client.BeginConnect(IPAddress.Loopback, owner.port, null, null);
                    using (connecting.AsyncWaitHandle)
                    { if (!connecting.AsyncWaitHandle.WaitOne(5000)) throw new IOException(); }
                    client.EndConnect(connecting);
                    client.NoDelay = true;
                    client.SendTimeout = 5000; client.ReceiveTimeout = 310000;
                    lock (gate) { if (closed) return; ready = true; }
                    owner.Send(2, id, new byte[0]);
                    ThreadPool.QueueUserWorkItem(delegate { WriteLoop(); });
                    NetworkStream socket = client.GetStream();
                    while (true)
                    {
                        int allowance;
                        lock (gate)
                        {
                            while (!closed && credit == 0)
                                if (!Monitor.Wait(gate, 310000)) throw new IOException();
                            if (closed) return;
                            allowance = Math.Min(credit, Chunk);
                        }
                        byte[] data = new byte[allowance];
                        int count = socket.Read(data, 0, data.Length);
                        if (count == 0) return;
                        lock (gate) { credit -= count; }
                        if (count != data.Length) Array.Resize(ref data, count);
                        owner.Send(3, id, data);
                    }
                }
                catch { }
                finally { Close(); }
            }
            private void WriteLoop()
            {
                try
                {
                    while (true)
                    {
                        byte[] data;
                        lock (gate)
                        {
                            while (!closed && count == 0) Monitor.Wait(gate);
                            if (closed) return;
                            data = new byte[Math.Min(count, Chunk)];
                            for (int i = 0; i < data.Length; i++) { data[i] = incoming[head]; head = (head + 1) % Window; }
                            count -= data.Length;
                        }
                        client.GetStream().Write(data, 0, data.Length);
                        lock (gate) { receiveCredit += data.Length; }
                        owner.Credit(id, data.Length);
                    }
                }
                catch { }
                finally { Close(); }
            }
            internal void Accept(byte[] data)
            {
                lock (gate)
                {
                    if (closed) return;
                    if (!ready || data.Length > receiveCredit) throw new IOException();
                    receiveCredit -= data.Length;
                    if (count + data.Length > Window) throw new IOException();
                    for (int i = 0; i < data.Length; i++) { incoming[tail] = data[i]; tail = (tail + 1) % Window; }
                    count += data.Length; Monitor.PulseAll(gate);
                }
            }
            internal void AddCredit(uint amount)
            {
                lock (gate)
                {
                    if (amount == 0 || amount > Window - credit) throw new IOException();
                    credit += (int)amount; Monitor.PulseAll(gate);
                }
            }
            internal void Close()
            {
                lock (gate)
                {
                    if (closed) return;
                    closed = true; Monitor.PulseAll(gate);
                    client.Close();
                }
                Channel removed; owner.channels.TryRemove(id, out removed);
                try { owner.Send(5, id, new byte[0]); } catch { }
            }
        }
    }
}
