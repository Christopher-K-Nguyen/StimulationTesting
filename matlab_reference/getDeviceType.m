function [File,isQuit] = getDeviceType(File)
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmDeviceType = false;
isQuit = false;
numOfChannels = 16;

%% Device Type
omneticsUTD = transpose(1:numOfChannels);
omneticsNNX = [1;2;3;4;13;14;15;16;5;6;7;8;9;10;11;12];
utd_plexon = [15;13;11;9;7;5;3;1;16;14;12;10;8;6;4;2];
plexon_idx = zeros(numOfChannels,1);
for channel_idx = 1:numOfChannels
    plexonChannel = utd_plexon(channel_idx);
    plexon_idx(channel_idx) = find(omneticsUTD == plexonChannel);
end
neuronexus_plexon = omneticsNNX(plexon_idx);

%% Device Type Selection
% Device Type List
deviceType_cell = { ...
    'UTD_MEA','UTD_Test', ...
    'Blackrock_Omnetics','Blackrock_PCB', ...
    'MicroProbes', ...
    'NeuroNexus', ...
    'Other'};
initialChoice = [];
% Subject Select
while ~confirmDeviceType
    fprintf('Enter device type selection...');
    % Confirm device type selection
    titleListSubject = 'Subject Selection';      % list title
    % Dialog box
    promptList = {...                     % list prompts
        'Select device type.'};  % insrtuction
    [deviceTypeList_idx,deviceTypeList_tf] = listdlg(...	% list dialog
        'PromptString',promptList,... % list prompts
        'ListString',deviceType_cell,...    % list
        'Name',titleListSubject,...
        'InitialValue',initialChoice,...
        'SelectionMode','single', ...
        'ListSize',[150 100]);

    % Collect input
    if ~deviceTypeList_tf             % cancel detected
        fprintf('\nQuitting...'); % quitting
        isQuit = true;
        break;                     % exit program
    else
        initialChoice = deviceTypeList_idx;
    end
    deviceType = deviceType_cell{deviceTypeList_idx};
    deviceType_fix = strrep(deviceType,'_','\_');

    % Confirm device type selection
    % Format questions
    promptQuestDeviceSelect = sprintf('Device type selected: {\\bf%s}',deviceType_fix);
    % Question box
    questDeviceSelect = questdlg(...                      % question dialog
        promptQuestDeviceSelect,...                       % question prompts
        'Confirm Device Type Selection',...                        % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questDeviceSelect                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmDeviceType = true;               % confirm info
            fprintf('%s\n',deviceType);  % info confirmed
        case BUTTON_TRY                             % try again
            confirmDeviceType = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            isQuit = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            isQuit = true;
            break;                                 % exit program
    end
end
if isQuit
    return;
end

if confirmDeviceType
    switch deviceType
        case {'UTD_MEA','Blackrock_Omnetics','MicroProbes'}
            channel_arr = omneticsUTD;
            % stimChannel_arr = utd_plexon;
            stimChannel_arr = omneticsUTD;
            switch deviceType
                case 'UTD_MEA'
                    channelMapping = [ ...
                        15  0   13  0   11  0   9; ...
                        8   0   6   0   4   0   2; ...
                        7   0   5   0   3   0   1; ...
                        16  0   14  0   12  0   10];
                    mappingDescription = [ ...
                        '16-channel, 4-shank array, ' ...
                        'top to bottom of shank. ' ...
                        '0 indicates large gaps'];
                case 'UTD_Test'
                    channelMapping = [ ...
                        1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16];
                    mappingDescription = ...
                        '16-channel, linear array';
                case 'Blackrock_Omnetics'
                    channelMapping = [ ...
                        13  14  15  16; ...
                        9   10  11  12; ...
                        5   6   7   8; ...
                        1   2   3   4];
                    mappingDescription = [ ...
                        '16-channel, 4x4-array, ' ...
                        'from pad side, ' ...
                        'wire bundle at bottom.'];
                case 'MicroProbes'
                    channelMapping = [ ...
                        16  15  14  0   0   0; ...
                        13  12  11  10  9   0; ...
                        0   8   7   6   5   0; ...
                        0   0   4   3   2   1];
                    mappingDescription = ...
                        '16-channel array. 0 indicates empty space';
            end
        case 'NeuroNexus'
            channel_arr = omneticsNNX;
            stimChannel_arr = neuronexus_plexon;
            channelMapping = [ ...
                1   0   7   0   13  0   14; ...
                3   0   4   0   10  0   16; ...
                2   0   8   0   2   0   11; ...
                6   0   5   0   9   0   15];
            mappingDescription = ['16-channel, 4-shank array (A4x4), ' ...
                'top to bottom of shank. 0 indicates large gaps'];
        case 'Blackrock_PCB'
            channel_arr = omneticsUTD;
            stimChannel_arr = omneticsUTD;
            channelMapping = [ ...
                8   16  9   1; ...
                7   15  10  2; ...
                6   14  11  3; ...
                5   13  12  4];
            mappingDescription = [ ...
                '16-channel, 4x4-array, ' ...
                'from pad side, ' ...
                'wire bundle at bottom.'];
        case {'UTD_Test','Other'}
            channel_arr = omneticsUTD;
            stimChannel_arr = omneticsUTD;
            channelMapping = [];
            mappingDescription = '';
    end
    [mapRow,mapCol] = size(channelMapping);
    amplitudeMapping = zeros(mapRow,mapCol);
    chargePhaseMapping = zeros(mapRow,mapCol);
    chargeInjectionMapping = zeros(mapRow,mapCol);
end

%% Store
File.Parameters.Device = deviceType;
File.Parameters.NumberOfChannels = numOfChannels;
File.Parameters.Channels.(deviceType) = channel_arr;
File.Parameters.Channels.Plexon = stimChannel_arr;
File.Parameters.Mapping.Description = mappingDescription;
File.Parameters.Mapping.Channel = channelMapping;
File.Parameters.Mapping.Amplitude = amplitudeMapping;
File.Parameters.Mapping.ChargePhase = chargePhaseMapping;
File.Parameters.Mapping.ChargeInjection = chargeInjectionMapping;

end