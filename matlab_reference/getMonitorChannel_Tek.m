function  monitorChannel = getMonitorChannel_Tek(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm';
BUTTON_CANCEL = 'Cancel';
BUTTON_TRY = 'Try Again';
opts.Interpreter = 'tex';   % option LaTeX
DIMS = [140 80];

%% Variables
channelSelect_cell = File.Oscilloscope.Channels;
channelName_cell = File.Oscilloscope.ChannelNames;
confirmMonitorChannel = false;

%% Function
while ~confirmMonitorChannel
    fprintf('Select oscilloscope monitor channel...');
    % Dialog box
    promptMonitorChannel = 'Select oscilloscope monitor channel.';     % instruction
    [monitorChannel_idx,~] = listdlg(...	% list dialog
        'PromptString',promptMonitorChannel,... % list prompts
        'ListString',channelSelect_cell,...    % list
        'Name','Oscilloscope Monitor Channel Selection',...                 % list title
        'ListSize',DIMS, ....
        'InitialValue',monitorChannel_idx, ...
        'SelectionMode','single');

    % Collect input
    if isempty(monitorChannel_idx)  % cancel detected
        break;                      % exit program
    end

    % Confirm oscilloscope selection
    % Format questions
    monitorChannel = channelSelect_cell{monitorChannel_idx};
    monitorChannelName = channelName_cell{monitorChannel_idx};
    monitorChannel_use = sprintf('%s %s',monitorChannel,monitorChannelName);
    channelPrompt = sprintf('Monitor Channel: {\\bf%s}',monitorChannel_use);
    % Question box
    opts.Default = BUTTON_CONFIRM;
    questMonitorChannel = questdlg(...                      % question dialog
        channelPrompt,...                       % question prompts
        'Confirm Oscilloscope Monitor Channel Selection',...% question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questMonitorChannel                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmMonitorChannel = true;               % confirm info
            fprintf('%s\n',monitorChannel_use);  % info confirmed
        case BUTTON_TRY                             % try again
            confirmMonitorChannel = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            break;                                 % exit program
        otherwise                                   % cancel
            break;                                 % exit program
    end
end

end