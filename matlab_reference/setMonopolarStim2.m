function isQuit = setMonopolarStim2(File,channelNum,pattern,varargin)
%% Variables
if ~isempty(varargin)
    tag = varargin{1};
else
    tag = 'rect';
end
if ~isnumeric(tag)
    if contains2(tag,{'rect','default','normal'})
        type = 0;
    elseif contains2(tag,'arb')
        type = 1;
    end
end

plexonChannel_arr = File.Parameters.Channels.Plexon;
% testChannel_arr = File.Parameters.Channels.Test;
plexonChannel = plexonChannel_arr(channelNum);
% amplitude1 = pattern.A1;
% amplitude2 = pattern.A2;
zeroChannel_arr = 1:16;
zeroChannel_arr(plexonChannel) = [];
zeroPattern = pattern;
zeroPattern.A1 = 0;
zeroPattern.A2 = 0;
isQuit = false;

%% Function
fprintf('Setting pattern to...');
startTime = tic;
PS_SetPatternType(1,plexonChannel,type);
switch type
    case 0
        PS_SetRectParam2(1,plexonChannel,pattern);% get errors
        % [pattern_check,~] = PS_GetRectParam2(1,plexonChannel);
        % amplitude1_check = pattern_check.Amp1;
        % amplitude2_check = pattern_check.Amp2;
        % isA1PolarityGood = sign(amplitude1) == sign(amplitude1_check);
        % isA2PolarityGood = sign(amplitude2) == sign(amplitude2_check);
        % isGood = isA1PolarityGood || isA2PolarityGood;
    case 1
        filename = makeArbitrary(File,pattern);
        PS_LoadArbPattern(1,channelNum,filename);
end
[isGood,~] = PS_IsWaveformBalanced(1,plexonChannel);
while ~isGood
    switch type
        case 0
            PS_SetRectParam2(1,plexonChannel,pattern);% get errors
            % [pattern_check,~] = PS_GetRectParam2(1,plexonChannel);
            % amplitude1_check = pattern_check.Amp1;
            % amplitude2_check = pattern_check.Amp2;
            % isA1PolarityGood = sign(amplitude1) ~= sign(amplitude1_check);
            % isA2PolarityGood = sign(amplitude2) ~= sign(amplitude2_check);
            % isGood = isA1PolarityGood || isA2PolarityGood;
        case 1
            filename = makeArbitrary(File,pattern);
            PS_LoadArbPattern(1,channelNum,filename);
    end
    [isGood,~] = PS_IsWaveformBalanced(1,plexonChannel);
end
for zeroChannelNum = zeroChannel_arr
    PS_SetPatternType(1,zeroChannelNum,type);
    switch type
        case 0
            PS_SetRectParam2(1,zeroChannelNum,zeroPattern);
        case 1
            filename = makeArbitrary(File,zeroPattern);
            PS_LoadArbPattern(1,zeroChannelNum,filename);
    end
end
[endTime,unit] = getEndTime(startTime);
fprintf('Channel %d (%.2f %s)\n',channelNum,endTime,unit);

end