function [isAnteDataBad,isPostDataBad] = checkWaveform(File,data,varargin)
%% Start
numOfVar = length(varargin);
if numOfVar > 0
    scopeChannel = varargin{1};
    fprintf('Checking %s waveform...',scopeChannel);
else
    fprintf('Checking waveform...');
end
startTime = tic;

try
    %% Variables
    % Channel
    channel_arr = [File.Data(:).Channel];
    numOfChannels = length(channel_arr);
    channelNum = channel_arr(numOfChannels);

    % Capture
    capture_arr = [File.Data(channelNum).Capture(:).Index];
    numOfCaptures = length(capture_arr);
    captureNum = capture_arr(numOfCaptures);

    % Time
    time = File.Data(channelNum).Capture(captureNum).Time;
    

    % Pattern
    phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
    pulseWidth = File.Parameters.PulseWidth;
    preDischarge = File.Parameters.BeforeDischarge;
    if phaseWidth1 < 100
        beforePulse_shift = 2;
        afterPulse_shift = 5;
        slopeCheck = 0.2;
    else
        beforePulse_shift = 10;
        afterPulse_shift = 20;
        slopeCheck = 0.02;
    end

    %% Check Waveform
    % Prepulse
    beforePulse_tf = time < -beforePulse_shift;
    antePulseTime = time(beforePulse_tf);
    antePulseData = data(beforePulse_tf);               % prepulse data
    data_ante_slope = getLinReg(antePulseTime,antePulseData);
    data_ante_slope_mag = abs(data_ante_slope);
    isAnteDataBad = data_ante_slope_mag > slopeCheck;

    % Postpulse
    if preDischarge > 0
        isPostDataBad = false;
    else
        try
            afterPulse_tf = time > pulseWidth + afterPulse_shift;
            postPulseTime = time(afterPulse_tf);
            postPulseData = data(afterPulse_tf);               % antepulse data
            data_post_slope = getLinReg(postPulseTime,postPulseData);
            data_post_slope_mag = abs(data_post_slope);
            isPostDataBad = data_post_slope_mag > slopeCheck;
        catch
            isPostDataBad = false;
        end
    end
catch
    isAnteDataBad = true;
    isPostDataBad = true;
end
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

end