function [isAnteDataBad,isPostDataBad] = checkWaveformEnds(File,scopeChannel)
%% Start
fprintf('Checking %s waveform...',scopeChannel);
startTime = tic;
try
%% Variables
channel_arr = [File.Data.Channel];
channelNum = length(channel_arr);
capture_arr = [File.Data(channelNum).Capture.Index];
captureNum = length(capture_arr);
time = File.Data(channelNum).Capture(captureNum).Time;
slopeCheck = 0.01;
switch scopeChannel
    case 'CH1'
        data = File.Data(channelNum).Capture(captureNum).Voltage;
        %             offset = 0.01;
    case 'CH2'
        data = File.Data(channelNum).Capture(captureNum).Current;
        %         slopeCheck_pow = 1;
        slopeCheck = 1;
        %         offset = 5;
    case 'CH3'
        data = File.Data(channelNum).Capture(captureNum).Working;
        %             offset = 0.25;
    case 'CH4'
        data = File.Data(channelNum).Capture(captureNum).Counter;
        %             offset = 0.1;
end
%         slopeCheck_pow = -1;
data_mag = abs(data);
% data_avg = mean(data_mag);
% slopeCheck = 10^slopeCheck_pow;

% Pattern
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
if phaseWidth1 >= 100
    beforePulse_shift = 10;
    afterPulse_shift = 20;
else
    beforePulse_shift = 2;
    afterPulse_shift = 5;
end
pulseWidth = phaseWidth1 + phaseWidth2 + interphaseDelay;

%% Check Waveform
% Prepulse
beforePulse_tf = time < -beforePulse_shift;
antePulseTime = time(beforePulse_tf);
antePulseData = data(beforePulse_tf);               % prepulse data
[data_ante_slope,~,~] = getLinReg(antePulseTime,antePulseData);
data_ante_slope_mag = abs(data_ante_slope);
isAnteDataBad = data_ante_slope_mag > slopeCheck;

% Postpulse 
try
    afterPulse_tf = time > pulseWidth + afterPulse_shift;
    postPulseTime = time(afterPulse_tf);
    postPulseData = data(afterPulse_tf);               % antepulse data
    [data_post_slope,~,~] = getLinReg(postPulseTime,postPulseData);
    data_post_slope_mag = abs(data_post_slope);
    isPostDataBad = data_post_slope_mag > slopeCheck;
catch
    isPostDataBad = false;
end
catch
    isAnteDataBad = true;
    isPostDataBad = true;
end
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

end