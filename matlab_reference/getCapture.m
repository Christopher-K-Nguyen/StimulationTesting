function File = getCapture(File,presentTime)
%% getCapture  Grab & store frames from File.Camera.Object
%   File = getCapture(File,presentTime) will:
%     • On first call: create VideoWriter in File.Camera.Writer
%       and init File.Video.Time/Frame
%     • Each call (if File.Camera.Enable):
%         – 'continuous': grab & write every frame
%         – 'periodic'  : grab & write whenever presentTime ≥ next time
%       also cache the first MEM_CACHE frames in File.Video.Frame

%% Constants
MEM_CACHE = 10;      % how many frames to keep in RAM

%% Early exit if camera not enabled
camState = File.Camera.Enable;
if ~camState
    return;
end

%% Grab & store according to mode
cam = File.Camera.Object;
vw = File.Camera.Writer;
mode = File.Camera.Mode;
frame_cell = File.Video.Frame;
cache = numel(frame_cell);
nextCache = cache + 1;

switch lower(mode)
    case 'continuous'
        % every call, grab & write one frame
        img = snapshot(cam);
        writeVideo(vw,img);

        % cache first MEM_CACHE frames
        if cache < MEM_CACHE
            File.Video.Time(nextCache,1) = presentTime;
            File.Video.Frame{nextCache,1} = img;
        end

    case 'periodic'
        % only when presentTime ≥ NextCaptureTime
        next = File.Camera.Next;
        if presentTime >= next
            interval = File.Camera.Interval;
            img = snapshot(cam);
            writeVideo(vw,img);

            % cache first MEM_CACHE frames
            if cache < MEM_CACHE
                File.Video.Time(nextCache,1) = presentTime;
                File.Video.Frame{nextCache,1} = img;
            end

            % schedule next capture
            File.Camera.Next = next + interval;
        end

    otherwise
        error('Unknown File. Camera.Mode: %s', mode);
end

end
