function quitProgram = setMultipolarStim(stimNum,channelStim,channelGround,pattern)
%% Constants
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
zeroChannel_arr = 1:16;
zeroChannel_arr(channelStim) = [];
if channelGround == 0
    zeroChannel_arr = [];
else
    zeroChannel_arr(channelGround) = [];
end
zeroPattern = pattern;
zeroPattern.A1 = 0;
zeroPattern.A2 = 0;
quitProgram = false;

%% Function
fprintf('Setting pattern to Channel %d...',channelStim);
errSetRectParam = 1;
while errSetRectParam ~= 0                             % setting parameters
    errSetRectParam = PS_SetRectParam2(stimNum,channelStim,pattern);% get errors
    if ~isempty(channelGround)
        for offChannelNum = zeroChannel_arr
            PS_SetRectParam2(stimNum,offChannelNum,zeroPattern);
        end
    end
    switch errSetRectParam                               % getting errors
        case 0                              % no errors
            fprintf('OK.\n'); % loaded channel stimulation
        case -1                                                         % errors found
            msg = 'INVALID ARGUMENT(S)';                                % invalide argument(s)
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errSetRectParam ~= 0
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('Quitting...\n\n');	% quitting
                quitProgram = true;
                return;                     % exit program
            case BUTTON_TRY               	% try again
                fprintf('Trying again...\n\n');% trying again
                %                 try
                % 	                pause(5);
                %                     PS_InitAllStim();
                %                 catch
                %                 end
            otherwise                       % cancel
                fprintf('Quitting...\n\n');	% quitting
                quitProgram = true;
                return;                     % exit program
        end
    end
end


end